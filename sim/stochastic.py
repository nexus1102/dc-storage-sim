"""Two-stage extensive-form optimization; all first-stage variables are shared objects."""
import linopy
import numpy as np
import pandas as pd
import xarray as xr
from config import Config
from dispatch import build_block,extract,optimize

def scenarios(load,price,n=4,seed=20261110,band=.1):
    if n<1 or not 0<=band<1: raise ValueError('invalid scenario count/band')
    rng=np.random.default_rng(seed)
    return [(load*rng.uniform(1-band,1+band),np.asarray(price)*rng.uniform(1-band,1+band)) for _ in range(n)]

def solve_two_stage(cases,modules=6,cfg=Config(),probabilities=None,radius=0.,**options):
    count=len(cases)
    if count<1: raise ValueError('no scenarios')
    q=np.full(count,1/count) if probabilities is None else np.asarray(probabilities,float)
    if len(q)!=count or (q<0).any() or abs(q.sum()-1)>1e-10: raise ValueError('invalid probabilities')
    if not 0<=radius<=2: raise ValueError('L1 radius must be in [0,2]')
    m=linopy.Model(); blocks=[]; shared=None
    for i,(load,price) in enumerate(cases):
        b=build_block(m,load,price,modules,cfg,prefix=f's{i}',shared=shared,**options)
        if shared is None: shared={k:b.variables[k] for k in ('reg_up','reg_dn','reserve_commit')}
        blocks.append(b)
    if radius==0:
        objective=sum(float(p)*b.cost for p,b in zip(q,blocks))
    else:
        # Dual of max_{p>=0,sum p=1, ||p-q||_1<=radius} p^T cost(x).
        lam=m.add_variables(lower=-np.inf,name='dro_lambda')
        gamma=m.add_variables(lower=0,name='dro_gamma')
        ix=pd.Index(range(count),name='scenario')
        a=m.add_variables(lower=0,coords=[ix],name='dro_a')
        b=m.add_variables(lower=0,coords=[ix],name='dro_b')
        for i,block in enumerate(blocks):
            m.add_constraints(lam+a[i]-b[i]>=block.cost,name=f'dro_cost_{i}')
            m.add_constraints(gamma>=a[i]+b[i],name=f'dro_radius_{i}')
        objective=lam+sum(float(q[i])*(a[i]-b[i]) for i in range(count))+radius*gamma
    m.add_objective(objective)
    bounds=optimize(m); results=[extract(b) for b in blocks]
    first={k:np.asarray(v.solution) for k,v in shared.items()}
    residual=max(float(np.max(np.abs(r['trace'][k]-first[k]))) for r in results for k in first)
    return dict(cost=float(m.objective.value),lo=bounds.lo,hi=bounds.hi,
                first=first,nonanticipation=residual,scenarios=results,probabilities=q)

def evaluate(first,cases,modules=6,cfg=Config(),**options):
    """Fixed bids and commitments; no refitting on evaluation samples."""
    records=[]
    for i,(load,price) in enumerate(cases):
        m=linopy.Model(); b=build_block(m,load,price,modules,cfg,soft=True,**options)
        for key,values in first.items():
            rhs=xr.DataArray(np.asarray(values),coords=[pd.Index(range(len(values)),name='t')])
            m.add_constraints(b.variables[key]==rhs,name=f'fixed_{key}')
        m.add_objective(b.cost)
        try:bounds=optimize(m)
        except RuntimeError as exc:
            if 'infeasible' not in str(exc):raise
            # A changed activation trajectory can make fixed bids physically
            # impossible, even with soft reserve penalties. Do not refit bids
            # or invent a finite feasible cash cost.
            records.append(dict(scenario=i,feasible=False,cost=np.nan,lo=np.nan,hi=np.nan,
                violation=1.,reserve_shortfall_mwh=np.nan,conditional_loss_probability=np.nan,
                eens_mwh_per_event=np.nan,reason='infeasible fixed-bid physical recourse'))
            continue
        r=extract(b)
        tr=r['trace']; need=tr.floor.to_numpy(); available=tr.soc.to_numpy()
        rng=np.random.default_rng(cfg.seed+5000+i)
        t=rng.integers(0,len(tr),4000)
        tau=rng.lognormal(np.log(20.),.6,4000)/3600
        tau[rng.random(4000)<.001]=cfg.ride_h
        event_load=tr.site.to_numpy()[t]*np.clip(rng.normal(1.,.05,4000),.85,1.15)
        # End-of-slot states: a fault at the start sees previous-slot SOC.
        event_soc=np.roll(tr.soc.to_numpy(),1)[t]
        short=np.maximum(0.,event_load*tau-np.minimum(event_soc*cfg.eta,r['power']*tau))
        records.append(dict(scenario=i,feasible=True,cost=r['cost'],lo=bounds.lo,hi=bounds.hi,
                            violation=float((tr.reserve_slack>1e-6).mean()),
                            reserve_shortfall_mwh=float(np.maximum(need-available,0).mean()),
                            conditional_loss_probability=float((short>1e-9).mean()),
                            eens_mwh_per_event=float(short.mean())))
    return pd.DataFrame(records)
