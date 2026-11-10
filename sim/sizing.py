"""Exact finite-design MILP: dispatch value functions + binary design selection.

Every integer candidate is solved using the entire shared dispatch model. This
eliminates the continuous dispatch variables exactly for the finite candidate set;
the master is equivalent to the disjunctive integrated MILP. No heuristic search.
"""
import numpy as np
import pandas as pd
from scipy.optimize import milp,Bounds,LinearConstraint
from config import Config,BASELINE
from dispatch import solve
from economics import crf

def event_eens(result,load_multiplier=1.15,duration=.25):
    """Deterministic stress-event EENS, NOT an annual outage-frequency estimate."""
    tr=result['trace']; eta=result['config']['eta']
    need=tr.site.to_numpy()*load_multiplier*duration
    deliver=np.minimum(tr.soc.to_numpy()*eta,result['power']*duration)
    return float(np.maximum(need-deliver,0).mean())

def candidates(load,price,counts=range(5,65),cfg=Config(),**options):
    rows=[]
    for n in counts:
        try: r=solve(load,price,n,cfg,**options)
        except RuntimeError as exc:
            if 'infeasible' in str(exc): continue
            raise
        rows.append(dict(modules=n,energy=.5*n,n1=bool(2*(n-1)>=max(10.,load.sum(axis=1).max())),
                         cost=r['cost'],lo=r['lo'],hi=r['hi'],eens=event_eens(r,duration=cfg.ride_h),
                         annual_cost=365*r['cost']+.5*n*1000*cfg.capex_kwh*crf(cfg.discount,cfg.years)))
    return pd.DataFrame(rows)

def epsilon_frontier(load,price,counts,cfg=Config(),limits=(.1,.05,.02,.01,0.)):
    """Reoptimize dispatch WITH the reliability limit, rather than filter old dispatches."""
    rows=[]
    for eps in limits:
        table=candidates(load,price,counts,cfg, eens_limit=eps)
        if table.empty: continue
        chosen=select(table,eps); chosen['epsilon']=eps; rows.append(chosen)
    result=pd.DataFrame(rows)
    if result.empty: return result
    result=result.sort_values('annual_cost').reset_index(drop=True)
    keep=[]; best=float('inf')
    for i,row in result.iterrows():
        if row.eens<best-1e-10: keep.append(i); best=row.eens
    return result.loc[keep].reset_index(drop=True)

def select(table,eens_limit=np.inf,require_n1=True):
    d=table.copy()
    allowed=(d.eens<=eens_limit+1e-12)&(d.n1 if require_n1 else True)
    if not allowed.any(): raise ValueError('no feasible design under reliability bound')
    cost=d.annual_cost.to_numpy()
    result=milp(cost,integrality=np.ones(len(d)),bounds=Bounds(np.zeros(len(d)),allowed.astype(float)),
                constraints=LinearConstraint(np.ones((1,len(d))),1,1),options={'mip_rel_gap':1e-9})
    if not result.success: raise RuntimeError(result.message)
    chosen=d.iloc[int(np.argmax(result.x))].to_dict()
    chosen['master_gap']=float(result.mip_gap)
    chosen['dispatch_bound_width']=float(chosen['hi']-chosen['lo'])
    chosen['certified_annual_lo']=float((d.loc[allowed,'annual_cost']-365*(d.loc[allowed,'cost']-d.loc[allowed,'lo'])).min())
    chosen['certified_annual_hi']=float(chosen['annual_cost']+365*(chosen['hi']-chosen['cost']))
    chosen['candidate_min']=int(d.modules.min()); chosen['candidate_max']=int(d.modules.max())
    chosen['at_search_boundary']=bool(chosen['modules']==d.modules.max())
    return chosen

def pareto(table):
    rows=[]
    for eps in sorted(table.eens.unique(),reverse=True):
        try: r=select(table,eps)
        except ValueError: continue
        r['epsilon']=eps; rows.append(r)
    if not rows: return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates('modules').sort_values('annual_cost').reset_index(drop=True)

def weighted_sum(table,weights=(0.,1e4,1e5,1e6,1e7)):
    d=table[table.n1]
    return pd.DataFrame([dict(weight=w,modules=int(d.loc[(d.annual_cost+w*d.eens).idxmin(),'modules'])) for w in weights])

def legacy_anchors(load):
    """Historical-v2 contract checked independently of the corrected v3 model."""
    from reference import pipeline_v2 as p,topology
    ride,_,_=p.cvar_reserve(load.sum(axis=1).to_numpy(),p.gen_start_scenarios(seed=20261110))
    reference=pd.read_csv(BASELINE/'v2_reference.csv').query("policy == 'topology'").set_index('arch')
    rows=[]
    for name,arch in topology.ARCHITECTURES.items():
        r=p.solve_case(arch,'topology',load,p.PRICE.repeat(4),ride,reg_on=True,flex_on=True)
        error=abs(r['cost']-reference.loc[name,'cost'])
        rows.append(dict(arch=name,modules=arch.modules,cost=r['cost'],reference=reference.loc[name,'cost'],error=error,passed=error<1e-3))
    return pd.DataFrame(rows)
