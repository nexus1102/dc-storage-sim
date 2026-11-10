"""Power-duration capability as a projection of the FULL operational feasible set.

The grid-reduction event is measured against an explicit feasible reference.
Physical per-unit states before the event are fixed to that reference, avoiding
free initial energy. Recovery and terminal cyclic constraints remain in force.
"""
import numpy as np
import linopy
from scipy.optimize import milp,Bounds,LinearConstraint
from config import Config
from dispatch import build_block,extract,optimize

def standby_reference(load,price,topology,cfg=Config(),scope='module',contracts=None):
    """Declared backup-only baseline: no routine cycling, units at configured SOCmax.

    This is a scenario initial-state assumption, not an inferred observed SOC.
    Workload scheduling still respects exactly the same service contracts.
    """
    m=linopy.Model()
    b=build_block(m,load,price,len(topology.units),cfg,physical_topology=topology,
                  contingency_scope=scope,service_contracts=contracts,reg=False,flex=False)
    m.add_constraints(b.variables['charge']==0,name='backup_no_charge')
    m.add_constraints(b.variables['discharge']==0,name='backup_no_dispatch')
    for unit in topology.units:
        m.add_constraints(b.physical['soc'][unit.name]==cfg.soc_max*unit.energy*unit.soh,name='backup_soc_'+unit.name)
    m.add_objective(b.cost);bounds=optimize(m);r=extract(b)
    r.update(modules=len(topology.units),lo=bounds.lo,hi=bounds.hi)
    r['options']['reference_policy']='backup_only_at_configured_soc_max'
    return r

def deliverable_load(topology,soc,duration_h,fault,eta=.95):
    """Independent static network-flow/MILP oracle, MW of proportionate critical load.

No aggregate SOC substitution: sources retain their individual energy budgets.
Used for analytical counterexamples and independent outage auditing.
"""
    topology.validate()
    soc=np.asarray(soc,float)
    if duration_h<=0 or not np.isfinite(duration_h) or not 0<eta<=1 or len(soc)!=len(topology.units) or (soc<0).any() or not np.isfinite(soc).all():
        raise ValueError('invalid outage state')
    if not set(fault.units)<={u.name for u in topology.units} or not set(fault.paths)<={p.name for p in topology.paths}:
        raise ValueError('unknown failed component')
    edges=[(i,p) for i,u in enumerate(topology.units) if u.name not in fault.units for p in u.paths if p not in fault.paths]
    links=[(p.name,g.name) for p in topology.paths if p.name not in fault.paths for g in topology.groups if p.name in g.paths]
    switches=[e for e in edges if len(topology.units[e[0]].paths)>1]
    ne,nl,nz=len(edges),len(links),len(switches); nv=ne+nl+nz+1
    objective=np.zeros(nv);objective[-1]=-1.
    rows=[];lo=[];hi=[]
    def con(coeff,lower=-np.inf,upper=np.inf):
        a=np.zeros(nv)
        for k,v in coeff.items():a[k]=v
        rows.append(a);lo.append(lower);hi.append(upper)
    for i,u in enumerate(topology.units):
        con({k:1 for k,e in enumerate(edges) if e[0]==i},upper=min(soc[i]*eta,u.power*duration_h))
        ids=[switches.index(e)+ne+nl for e in switches if e[0]==i]
        if ids: con({k:1 for k in ids},upper=1)
    for k,e in enumerate(edges):
        if e in switches: con({k:1,ne+nl+switches.index(e):-topology.units[e[0]].power*duration_h},upper=0)
    for p in topology.paths:
        a={k:1 for k,e in enumerate(edges) if e[1]==p.name}
        con(a,upper=p.power*duration_h)
        a.update({ne+k:-1 for k,e in enumerate(links) if e[0]==p.name});con(a,0,0)
    for g in topology.groups:
        a={ne+k:1 for k,e in enumerate(links) if e[1]==g.name};a[nv-1]=-g.fraction*duration_h;con(a,0,0)
    upper=np.full(nv,np.inf);upper[ne+nl:ne+nl+nz]=1;upper[-1]=topology.design_load
    integer=np.zeros(nv);integer[ne+nl:ne+nl+nz]=1
    result=milp(objective,integrality=integer,bounds=Bounds(np.zeros(nv),upper),
                constraints=LinearConstraint(np.array(rows),lo,hi),options={'mip_rel_gap':1e-9})
    if not result.success: raise RuntimeError(result.message)
    return float(result.x[-1])

def envelope(reference,load,price,topology,start,duration_slots,cfg=Config(),scope='module',contracts=None,mode='joint'):
    n=len(load)
    if start<0 or duration_slots<1 or start+duration_slots>n: raise ValueError('event outside modeled day')
    if mode not in ('joint','battery_only'): raise ValueError('invalid capability attribution')
    m=linopy.Model()
    b=build_block(m,load,price,len(topology.units),cfg,physical_topology=topology,
                  contingency_scope=scope,service_contracts=contracts,reg=False,flex=False)
    p=m.add_variables(lower=0,upper=cfg.grid_max,name='firm_grid_reduction_mw')
    ref=reference['trace']; before=(start-1)%n
    states=reference['physical_trace'].query("kind == 'soc' and t == @before").set_index('key').value
    for unit in topology.units:
        m.add_constraints(b.physical['soc'][unit.name][before]==float(states[unit.name]),name='fixed_state_'+unit.name)
        for kind in ('soc','charge','discharge'):
            history=reference['physical_trace'].query('kind == @kind and key == @unit.name').sort_values('t').value.to_numpy()
            for t in range(start):m.add_constraints(b.physical[kind][unit.name][t]==float(history[t]),name=f'history_{kind}_{unit.name}_{t}')
    m.add_constraints(b.variables['cold'][before]==float(ref.cold.iloc[before]),name='fixed_thermal_state')
    for key in ('grid','it','ac','cold'):
        for t in range(start):m.add_constraints(b.variables[key][t]==float(ref[key].iloc[t]),name=f'history_{key}_{t}')
    if b.services is not None:
        for name,entry in b.services['jobs'].items():
            history=reference['service_trace'].query('job == @name').sort_values('t')
            # Soft-deadline penalties must never buy fictitious firm capacity.
            m.add_constraints(entry['unmet']<=float(history.unmet.iloc[0])+1e-9,name='no_extra_unmet_'+name)
            for variable,column in (('work','work_mw'),('on','on'),('start','start')):
                if entry[variable] is not None:
                    for t in range(start):m.add_constraints(entry[variable][t]==float(history[column].iloc[t]),name=f'history_{name}_{variable}_{t}')
    for t in range(start,start+duration_slots):
        m.add_constraints(b.variables['grid'][t]+p<=float(ref.grid.iloc[t]),name=f'event_delivery_{t}')
    if mode=='battery_only':
        for t in range(n):
            m.add_constraints(b.variables['it'][t]==float(ref.it.iloc[t]),name=f'fixed_it_{t}')
            m.add_constraints(b.variables['ac'][t]==float(ref.ac.iloc[t]),name=f'fixed_ac_{t}')
    m.add_objective(-p); bounds=optimize(m)
    maximum=float(p.solution)
    # Recover a concrete least-cost witness at the certified event power.
    # MIP feasibility tolerances can make the first optimum a few micro-MW
    # optimistic. Obtain a strictly feasible inner witness, report its margin.
    margin=max(1e-5,abs(maximum)*1e-6)
    guaranteed=max(0.,maximum-margin)
    m.add_constraints(p>=guaranteed,name='optimal_firm_power')
    m.objective=b.cost;optimize(m)
    witness=extract(b);witness['modules']=len(topology.units)
    return dict(power_mw=guaranteed,power_lo=max(0.,min(guaranteed,-bounds.hi)),power_hi=max(0.,-bounds.lo),
                witness_margin_mw=margin,duration_h=duration_slots*cfg.dt,delivered_mwh=guaranteed*duration_slots*cfg.dt,
                incremental_cost=witness['cost']-reference['cost'],mode=mode,witness=witness,
                definition='constant grid-import reduction vs explicit baseline, fixed pre-event unit SOC, cyclic recovery')
