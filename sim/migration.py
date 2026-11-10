"""Divisible multi-node job flow with explicit migration bandwidth and latency cost."""
import numpy as np
import pandas as pd
from scipy.optimize import linprog
from scipy.sparse import lil_matrix

def solve_migration(load,prices,capacity,bandwidth=1.,migration_cost=20.,latency_slots=1,dt=.25,enabled=True):
    load=np.asarray(load,float); prices=np.asarray(prices,float); cap=np.broadcast_to(capacity,load.shape)
    if load.shape!=prices.shape or load.ndim!=2 or (load<0).any() or (cap<0).any() or bandwidth<0 or latency_slots<0:
        raise ValueError('invalid node arrays')
    nodes,n=load.shape
    arcs=[(i,t,j,s) for i in range(nodes) for t in range(n) for j in range(nodes)
          for s in range(t,min(n,t+latency_slots+1)) if (enabled or i==j)]
    aeq=lil_matrix((nodes*n,len(arcs))); aub=lil_matrix((2*nodes*n,len(arcs)))
    costs=[]; bounds=[]
    for k,(i,t,j,s) in enumerate(arcs):
        aeq[i*n+t,k]=1; aub[j*n+s,k]=1
        if i!=j: aub[nodes*n+i*n+t,k]=1
        costs.append(prices[j,s]+(migration_cost*(1+s-t) if i!=j else 0.))
        bounds.append((0,None))
    b=np.r_[(cap*dt).ravel(),np.full(nodes*n,bandwidth*dt)]
    r=linprog(costs,A_eq=aeq.tocsr(),b_eq=(load*dt).ravel(),A_ub=aub.tocsr(),b_ub=b,bounds=bounds,method='highs')
    if not r.success: raise RuntimeError(r.message)
    df=pd.DataFrame([dict(source=i,release=t,destination=j,execute=s,mwh=float(r.x[k])) for k,(i,t,j,s) in enumerate(arcs) if r.x[k]>1e-9])
    return dict(cost=float(r.fun),conservation=float(abs(r.x.sum()-load.sum()*dt)),
                migrated_mwh=float(df.loc[df.source!=df.destination,'mwh'].sum()),flows=df)

if __name__=='__main__':
    from pathlib import Path
    from config import ROOT,INPUTS,load_inputs,tariff
    from intervals import bootstrap
    from runctx import RunContext
    run=RunContext(ROOT,prefix='v3migration')
    run.freeze_input(INPUTS/'load_business_24h.csv'); run.write_provenance()
    run.write_config(dict(nodes=2,bandwidth_mw=1.,migration_cost=20.,latency_slots=1,scenarios=40,seed=20261110)); run.write_env()
    base=load_inputs().sum(axis=1).to_numpy()/2
    rng=np.random.default_rng(20261110); rows=[]
    for scenario in range(40):
        load=np.stack([base,base])*rng.uniform(.9,1.1,(2,1))
        price=np.stack([tariff(),np.roll(tariff(),16)])*rng.uniform(.9,1.1,(2,1))
        a=solve_migration(load,price,capacity=4.,enabled=False)
        b=solve_migration(load,price,capacity=4.)
        rows.append(dict(scenario=scenario,local=a['cost'],cross_node=b['cost'],increment=a['cost']-b['cost'],
                         conservation=b['conservation'],migrated_mwh=b['migrated_mwh']))
        if scenario==0: b['flows'].to_csv(run.dir/'flows.csv',index=False)
    df=pd.DataFrame(rows); df.to_csv(run.dir/'migration.csv',index=False)
    mean,lo,hi=bootstrap(df.increment)
    pd.DataFrame([dict(mean=mean,lo=lo,hi=hi)]).to_csv(run.dir/'migration_value.csv',index=False)
    pd.DataFrame([dict(arch='two_nodes',policy='migration',check='work_conservation',residual=df.conservation.max(),tol=1e-6,**{'pass':df.conservation.max()<1e-6})]).to_csv(run.dir/'checks_v3.csv',index=False)
    run.seal(); run.publish([p.name for p in run.dir.iterdir() if p.is_file()],ROOT/'results/v3_migration')
    print(f'Migration benefit: {mean:.3f} [{lo:.3f}, {hi:.3f}] CNY/day')
