"""Shared linopy dispatch kernel for deterministic, stochastic and rolling models.

All exported states are END-of-slot; reserve is required over the following window.
Only day-ahead regulation and reserve commitment are shared between scenarios.
"""
from dataclasses import dataclass
from dataclasses import replace
import logging
import numpy as np
import pandas as pd
import xarray as xr
import linopy
import highspy
from business import BUSINESS
from config import Config, KEYS
from degradation import add_cost,unit_cost,calibration
from intervals import Interval

logging.getLogger('linopy').setLevel(logging.ERROR)

def window_weights(n, horizon, dt):
    """Circular look-ahead integration, including fractional last slots."""
    if horizon < 0 or horizon > n*dt:
        raise ValueError('reserve horizon must lie within modeled day')
    w = np.zeros((n,n))
    for t in range(n):
        left = horizon
        for k in range(n):
            h = min(dt,left)
            if h<=1e-12: break
            w[t,(t+1+k)%n] = h
            left -= h
    return w

@dataclass
class Block:
    prefix: str
    variables: dict
    cost: object
    load: pd.DataFrame
    price: np.ndarray
    cfg: Config
    energy: float
    power: float
    options: dict
    physical: dict | None=None
    services: dict | None=None

def build_block(m, load, price, modules, cfg=Config(), *, prefix='s0', reg=True,
                flex=True, thermal=True, policy='dynamic', degradation='sos2',
                shared=None, initial=None, terminal=None, soft=False, capacity_soh=1.,
                calibration_path=None, eens_limit=None,business_override=None,
                physical_topology=None,contingency_scope='module',service_contracts=None):
    cfg.validate()
    load = load.reset_index(drop=True).copy()
    price = np.asarray(price,float)
    n = len(load)
    if n==0 or list(load.columns)!=list(KEYS) or len(price)!=n:
        raise ValueError('load columns/order and price length mismatch')
    if not np.isfinite(load.to_numpy()).all() or (load.to_numpy()<0).any() or not np.isfinite(price).all():
        raise ValueError('invalid load or price')
    if modules < 1 or modules != int(modules): raise ValueError('modules must be positive integer')
    if not 0<capacity_soh<=1: raise ValueError('capacity SOH must be in (0,1]')
    if physical_topology is not None:
        physical_topology.validate()
        if len(physical_topology.units)!=modules: raise ValueError('module count and physical units disagree')
        if initial is not None or terminal is not None or capacity_soh!=1:
            raise ValueError('physical mode currently requires cyclic unit states; specify unit SOH in topology')
    e = .5*modules*capacity_soh
    p = min(2.*modules,10.)
    if physical_topology is not None:
        e=physical_topology.energy
        p=min(sum(u.power for u in physical_topology.units),sum(path.power for path in physical_topology.paths))
    ix = pd.Index(range(n),name='t')
    def da(a): return xr.DataArray(np.asarray(a),coords=[ix])
    v={}
    def var(key,lower=0,upper=np.inf):
        if isinstance(upper,np.generic): upper=float(upper)
        v[key]=m.add_variables(lower=lower,upper=upper,coords=[ix],name=prefix+'_'+key)
        return v[key]
    def con(key,expr,mask=None): m.add_constraints(expr,name=prefix+'_'+key,mask=mask)
    g=var('grid',upper=cfg.grid_max)
    ch=var('charge',upper=min(p,cfg.charge_rate*e))
    dis=var('discharge',upper=p)
    soc=var('soc',upper=cfg.soc_max*e)
    it=var('it')
    site=var('site')
    total=load.sum(axis=1).to_numpy()
    services=None
    if service_contracts is not None:
        if initial is not None: raise ValueError('explicit jobs require a full-day model, not aggregate MPC state')
        from service_contracts import add_services
        services=add_services(m,service_contracts,n,cfg.dt,prefix+'_service')
    net=0*it+da(total)
    for key in KEYS:
        spec=BUSINESS[key]
        if business_override and key in business_override: spec=replace(spec,**business_override[key])
        reference_mw=(initial or {}).get('reference_mw',{}).get(key,float(load[key].mean()))
        controlled=services is not None and key in services['powers']
        f=min(1.,spec.f*cfg.flex_scale) if flex and not controlled else 0.
        d=var('defer_'+key,upper=da(f*load[key].to_numpy()))
        r=var('recover_'+key,upper=spec.ramp_frac*cfg.recovery_scale*reference_mw if f else 0.)
        q=var('queue_'+key,upper=f*reference_mw*spec.window_h*cfg.window_scale)
        win=max(0,int(np.floor(spec.window_h*cfg.window_scale/cfg.dt+1e-9)))
        q0=0. if initial is None else initial.get('queue_'+key,0.)
        con('queue_init_'+key,q[0]==q0+cfg.dt*(d[0]-r[0]))
        con('queue_recursion_'+key,q-q.roll(t=1)==cfg.dt*(d-r),mask=da(np.arange(n)>0))
        # cumulative deadline is stronger than a queue capacity proxy
        due_old=np.zeros(n) if initial is None else np.array(initial.get('due_'+key,[q0]*n))
        due_new=d.cumsum('t').roll(t=win).where(da(np.arange(n)>=win),0) if win<n else 0*d
        con('deadline_'+key,cfg.dt*r.cumsum('t')>=cfg.dt*due_new+da(due_old))
        if initial is None or terminal is not None:
            con('queue_end_'+key,q[-1]==(0. if terminal is None else terminal.get('queue_'+key,0.)))
        net=net-d+r
        if controlled: net=net-da(load[key].to_numpy())+services['powers'][key]
    con('it_balance',it==net)
    ac=var('ac',upper=cfg.ac_max if thermal else 0.)
    cold=var('cold',upper=1.)
    alpha=float(np.exp(-cfg.dt*cfg.thermal_ua/cfg.thermal_c))
    k=(1-alpha)/(cfg.thermal_ua*cfg.thermal_range)
    ambient=(1-alpha)*(cfg.thermal_tmax-cfg.thermal_ambient)/cfg.thermal_range
    if thermal:
        con('thermal_init',cold[0]==alpha*(.5 if initial is None else initial['cold'])+k*(cfg.thermal_cop*ac[0]-it[0])+ambient)
        con('thermal_recursion',cold==alpha*cold.roll(t=1)+k*(cfg.thermal_cop*ac-it)+ambient,mask=da(np.arange(n)>0))
    else: con('cold_disabled',cold==.5)
    thermal_slack=m.add_variables(lower=0,upper=np.inf if soft else 0.,name=prefix+'_thermal_terminal_slack')
    v['thermal_terminal_slack']=thermal_slack
    if thermal:
        cold_target=.5 if terminal is None else terminal.get('cold',.5)
        con('cold_terminal_up',cold[-1]-cold_target<=thermal_slack)
        con('cold_terminal_dn',cold_target-cold[-1]<=thermal_slack)
    con('site_balance',site==it+ac)
    con('bus_balance',g+dis-ch==site)
    if initial is None:
        con('soc_recursion',soc==soc.roll(t=1)+cfg.dt*(cfg.eta*ch-dis/cfg.eta))
    else:
        con('soc_init',soc[0]==initial['soc']+cfg.dt*(cfg.eta*ch[0]-dis[0]/cfg.eta))
        con('soc_recursion',soc==soc.roll(t=1)+cfg.dt*(cfg.eta*ch-dis/cfg.eta),mask=da(np.arange(n)>0))
    slack=var('reserve_slack',upper=np.inf if soft else 0.)
    floor=var('floor')
    reserve_duration=cfg.ride_h if policy=='worst' else min(cfg.reserve_h,cfg.ride_h)
    w=window_weights(n,min(reserve_duration,n*cfg.dt),cfg.dt)
    redundancy=.5 if modules>=6 and physical_topology is None else 0.
    if policy=='fixed': rhs=2.5
    elif policy in ('dynamic','worst'):
        rhs=redundancy+0*site
        left=min(reserve_duration,n*cfg.dt)
        for j in range(n):
            h=min(cfg.dt,left)
            if h<=1e-12: break
            rhs=rhs+(h/cfg.eta)*site.roll(t=-(j+1)); left-=h
    elif policy=='static': rhs=da(w@total/cfg.eta+redundancy)
    else: raise ValueError(policy)
    con('floor_definition',floor==rhs)
    ru=var('reg_up',upper=p if reg else 0.) if shared is None else shared['reg_up']
    rd=var('reg_dn',upper=p if reg else 0.) if shared is None else shared['reg_dn']
    commit=var('reserve_commit') if shared is None else shared['reserve_commit']
    v.update(reg_up=ru,reg_dn=rd,reserve_commit=commit)
    con('commit_floor',commit+slack>=floor)
    con('reserve_floor',soc+slack>=commit)
    con('reg_power_up',dis+ru<=p)
    con('reg_power_dn',ch+rd<=min(p,cfg.charge_rate*e))
    con('reg_energy_up',soc+slack>=commit+ru*cfg.reg_duration/cfg.eta)
    con('reg_energy_dn',soc+rd*cfg.reg_duration*cfg.eta<=cfg.soc_max*e)
    physical=None
    if physical_topology is not None:
        from physical_topology import add_security
        physical=add_security(m,site,physical_topology,cfg,prefix+'_physical',contingency_scope)
        for key,aggregate in (('soc',soc),('charge',ch),('discharge',dis),('reg_up',ru),('reg_dn',rd)):
            con('physical_aggregate_'+key,aggregate==sum(physical[key].values()))
    if eens_limit is not None:
        if eens_limit<0: raise ValueError('negative EENS budget')
        short=var('event_short')
        con('event_energy',short>=site*(1.15*cfg.ride_h)-soc*cfg.eta)
        con('event_power',short>=site*(1.15*cfg.ride_h)-p*cfg.ride_h)
        con('event_eens',short.sum()<=float(eens_limit)*n)
    term=m.add_variables(lower=0,upper=np.inf if soft else 0.,name=prefix+'_terminal_slack')
    v['terminal_slack']=term
    if terminal is not None:
        con('terminal_hi',soc[-1]-terminal['soc']<=term)
        con('terminal_lo',terminal['soc']-soc[-1]<=term)
    peak=m.add_variables(lower=0. if initial is None else initial.get('peak',0.),name=prefix+'_peak')
    v['peak']=peak
    con('peak_bound',peak>=g)
    schedule_throughput=cfg.dt*.5*(ch+dis).sum()
    regulation_throughput=cfg.dt*cfg.activation_beta*(ru+rd).sum()
    throughput=schedule_throughput+regulation_throughput
    deg=add_cost(m,schedule_throughput,e,prefix+'_deg',mode=degradation,path=calibration_path)
    deg=deg+(15. if degradation=='legacy' else unit_cost(.3,calibration_path))*regulation_throughput
    capacity_rev=cfg.dt*cfg.reg_capacity_price*(ru+rd).sum()
    mileage_rev=cfg.dt*cfg.reg_mileage_price*cfg.mileage_ratio*cfg.performance*(ru+rd).sum()
    energy_cost=cfg.dt*(g*da(price)).sum()
    cost=energy_cost+cfg.demand_rate*peak+deg-capacity_rev-mileage_rev+cfg.penalty*(cfg.dt*slack.sum()+term+thermal_slack*cfg.thermal_c*cfg.thermal_range)
    if services is not None: cost=cost+services['cost']
    v.update(throughput=throughput,deg_cost=deg,capacity_rev=capacity_rev,mileage_rev=mileage_rev)
    return Block(prefix,v,cost,load,price,cfg,e,p,dict(reg=reg,flex=flex,thermal=thermal,policy=policy,
                 degradation=degradation,initial=initial,terminal=terminal,soft=soft,eens_limit=eens_limit,business_override=business_override,
                 calibration=[a.tolist() for a in calibration(calibration_path)],
                 physical_topology=physical_topology.snapshot() if physical_topology else None,
                 contingency_scope=contingency_scope,service_contracts=service_contracts.snapshot() if service_contracts else None),physical,services)

def optimize(m):
    # PyPSA may have initialized HiGHS with a different global thread count.
    highspy.Highs.resetGlobalScheduler(True)
    status,condition=m.solve(solver_name='highs',log_to_console=False,threads=1,
                             random_seed=0,mip_rel_gap=1e-8)
    if status!='ok' or condition!='optimal':
        raise RuntimeError(f'optimization failed: {status}/{condition}')
    h=m.solver_model
    info=h.getInfo()
    primal=float(m.objective.value)
    if m.type=='LP':
        lp=h.getLp(); sol=h.getSolution()
        cd=np.asarray(sol.col_dual); rd=np.asarray(sol.row_dual)
        cb=np.where(cd>=0,np.asarray(lp.col_lower_),np.asarray(lp.col_upper_))
        rb=np.where(rd>=0,np.asarray(lp.row_lower_),np.asarray(lp.row_upper_))
        # Duals below HiGHS' feasibility tolerance can have the wrong sign at
        # one-sided infinite bounds. They contribute zero, never 0*infinity.
        if (np.abs(cd[~np.isfinite(cb)])>1e-7).any() or (np.abs(rd[~np.isfinite(rb)])>1e-7).any():
            raise AssertionError('nonzero dual multiplier on an infinite bound')
        dual=float(lp.offset_+np.dot(cd[np.isfinite(cb)],cb[np.isfinite(cb)])+
                   np.dot(rd[np.isfinite(rb)],rb[np.isfinite(rb)]))
        if not np.isfinite(dual) or dual>primal+1e-3:
            raise AssertionError(f'invalid LP dual bound: primal={primal}, dual={dual}')
    else: dual=float(info.mip_dual_bound)
    pad=max(1e-6,abs(primal)*1e-10)
    return Interval(min(dual,primal)-pad,primal+pad)

def extract(block):
    v=block.variables
    def val(x): return np.asarray(x.solution,float)
    df=pd.DataFrame({k:val(x) for k,x in v.items() if getattr(x,'ndim',0)==1 and k not in ('throughput','deg_cost','capacity_rev','mileage_rev')})
    for k in KEYS: df['load_'+k]=block.load[k].to_numpy()
    df['price']=block.price
    cfg=block.cfg
    # Recompute cost using exported dispatch. Piecewise cost itself is exported separately.
    energy=float(cfg.dt*np.dot(df.grid,block.price))
    deg=float(val(v['deg_cost']))
    cap=float(val(v['capacity_rev'])); mil=float(val(v['mileage_rev']))
    penalty=cfg.penalty*(cfg.dt*df.reserve_slack.sum()+float(val(v['terminal_slack']))+float(val(v['thermal_terminal_slack']))*cfg.thermal_c*cfg.thermal_range)
    service_cost=0.
    if block.services is not None:
        from service_contracts import export_services
        service_cost=sum(float(entry['unmet'].solution)*entry['job'].unmet_price for entry in block.services['jobs'].values())
        for kind,power in block.services['powers'].items(): df['service_'+kind]=np.asarray(power.solution)
    demand=cfg.demand_rate*max(df.grid.max(),(block.options.get('initial') or {}).get('peak',0.))
    cost=energy+demand+deg-cap-mil+penalty+service_cost
    result=dict(cost=float(cost),energy_cost=energy,demand_cost=float(demand),
                deg_cost=deg,capacity_rev=cap,mileage_rev=mil,penalty=float(penalty),
                throughput=float(val(v['throughput'])),energy=block.energy,power=block.power,
                terminal_slack=float(val(v['terminal_slack'])),trace=df,
                thermal_terminal_slack=float(val(v['thermal_terminal_slack'])),
                config=cfg.snapshot(),options=block.options)
    # Optional extensions do not alter the historical V3 result schema.
    if block.physical is not None:
        from physical_topology import export_security
        result['physical_trace']=export_security(block.physical)
    if block.services is not None:
        result['service_trace']=export_services(block.services);result['service_cost']=service_cost
    return result

def solve(load,price,modules=6,cfg=Config(),**options):
    m=linopy.Model()
    b=build_block(m,load,price,modules,cfg,**options)
    m.add_objective(b.cost)
    bounds=optimize(m)
    if options.get('eens_limit') is not None:
        # Lexicographic augmentation removes weakly efficient epsilon solutions.
        primary=float(m.objective.value)
        m.add_constraints(b.cost<=primary+1e-7,name='epsilon_primary_optimum')
        m.objective=b.variables['event_short'].sum()/len(load)
        optimize(m)
    result=extract(b)
    result.update(lo=bounds.lo,hi=max(bounds.hi,result['cost']+1e-6),modules=modules)
    if options.get('eens_limit') is None and abs(result['cost']-float(m.objective.value))>1e-2:
        raise AssertionError('objective accounting mismatch')
    return result
