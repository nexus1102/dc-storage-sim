"""Standalone economic / reserve-constrained rolling dispatch, deliberately outside stages 0–3.

Carries battery, thermal and three queue states; historical cohorts carry deadlines.
Forecast errors are synthetic stressors, not a fitted real-world predictor.
"""
import argparse
from dataclasses import replace
import numpy as np
import pandas as pd
from config import Config,KEYS,load_inputs,tariff
from business import BUSINESS
from dispatch import solve
from degradation import unit_cost
from dispatch import build_block,extract,optimize
import linopy
import xarray as xr

def state(modules):
    return dict(soc=.5*modules*.5,cold=.5,**{'queue_'+k:0. for k in KEYS})

def day_ahead(load,price,modules,cfg):
    """24 h / 1 h two-stage upper layer, returned on the 15-minute grid."""
    from stochastic import scenarios,solve_two_stage
    ratio=int(round(1/cfg.dt))
    if len(load)%ratio: raise ValueError('hierarchical MPC requires whole hours')
    hourly=load.groupby(np.arange(len(load))//ratio).mean()
    pr=np.asarray(price).reshape(-1,ratio).mean(axis=1)
    upper=solve_two_stage(scenarios(hourly,pr,n=4,seed=cfg.seed),modules,replace(cfg,dt=1.),degradation='shallow')
    first={k:np.repeat(v,ratio) for k,v in upper['first'].items()}
    ref=np.repeat(np.mean([r['trace'].soc.to_numpy() for r in upper['scenarios']],axis=0),ratio)
    return first,ref

def rolling(load,price,modules=6,cfg=Config(),forecast_error=0.,horizon=16,seed=20261110,hierarchical=False):
    rng=np.random.default_rng(seed); n=len(load); current=state(modules); target=state(modules)
    history=[]; first=ref=None
    if hierarchical: first,ref=day_ahead(load,price,modules,cfg)
    for step in range(n):
        stop=min(n,step+horizon)
        pred=load.iloc[step:stop].reset_index(drop=True).copy()
        pp=np.asarray(price[step:stop]).copy()
        current['reference_mw']=load.mean().to_dict()
        if forecast_error:
            pred*=rng.uniform(1-forecast_error,1+forecast_error,len(pred))[:,None]
            pp*=rng.uniform(1-forecast_error,1+forecast_error,len(pred))
            pred.iloc[0]=load.iloc[step]; pp[0]=price[step]
        for k in KEYS:
            w=int(np.floor(BUSINESS[k].window_h*cfg.window_scale/cfg.dt+1e-9))
            done=sum(row['recover_'+k]*cfg.dt for row in history)
            current['due_'+k]=[max(0.,sum(row['defer_'+k]*cfg.dt for j,row in enumerate(history) if j<=step+t-w)-done) for t in range(stop-step)]
        if hierarchical:
            m=linopy.Model()
            b=build_block(m,pred,pp,modules,cfg,initial=current,terminal=target,soft=True,degradation='shallow')
            for key,bids in first.items():
                values=xr.DataArray(bids[step:stop],coords=[pd.Index(range(stop-step),name='t')])
                m.add_constraints(b.variables[key]==values,name='fixed_'+key)
            track=m.add_variables(lower=0,coords=[pd.Index(range(stop-step),name='t')],name='soc_track')
            reference=xr.DataArray(ref[step:stop],coords=[pd.Index(range(stop-step),name='t')])
            m.add_constraints(track>=b.variables['soc']-reference,name='track_up')
            m.add_constraints(track+b.variables['soc']>=reference,name='track_dn')
            # 1 CNY/MWh tracking regularizer; performance reports actual cash cost.
            m.add_objective(b.cost+cfg.dt*track.sum()); optimize(m); r=extract(b)
        else:
            try: r=solve(pred,pp,modules,cfg,initial=current,terminal=target,soft=True,degradation='shallow')
            except RuntimeError as exc: raise RuntimeError(f'MPC step {step}: {exc}') from exc
        row=r['trace'].iloc[0].to_dict(); row['step']=step
        row['terminal_slack']=r['terminal_slack']; row['thermal_terminal_slack']=r['thermal_terminal_slack']; history.append(row)
        current={k:float(row[k]) for k in ('soc','cold',*['queue_'+k for k in KEYS])}
        current['peak']=max((x['grid'] for x in history),default=0.)
    df=pd.DataFrame(history)
    throughput=cfg.dt*((1. if cfg.accounting_version==4 else .5)*(df.charge+df.discharge).sum()+cfg.activation_beta*(df.reg_up+df.reg_dn).sum())
    cost=cfg.dt*np.dot(df.grid,price)+cfg.demand_rate*df.grid.max()+throughput*unit_cost(.3)-cfg.dt*(cfg.reg_capacity_price+cfg.reg_mileage_price*cfg.mileage_ratio*cfg.performance)*(df.reg_up+df.reg_dn).sum()+cfg.penalty*cfg.dt*df.reserve_slack.sum()
    cost+=cfg.penalty*abs(current['soc']-target['soc'])
    cost+=cfg.penalty*abs(current['cold']-target['cold'])*cfg.thermal_c*cfg.thermal_range
    return dict(cost=float(cost),terminal_error=float(abs(current['soc']-target['soc'])),
                slack_rate=float((df.reserve_slack>1e-6).mean()),trace=df)

def value_experiment(load,price,modules=6,cfg=Config(),seeds=(20261110,20261111,20261112)):
    initial=state(modules)
    a=solve(load,price,modules,cfg,initial=initial,terminal=initial,degradation='shallow')
    b=rolling(load,price,modules,cfg)
    rows=[]
    for seed in seeds:
        c=rolling(load,price,modules,cfg,forecast_error=.1,seed=seed)
        rows.append(dict(seed=seed,open_loop=a['cost'],rolling_perfect=b['cost'],feedback=c['cost'],
                         delta_reopt=b['cost']-a['cost'],delta_feedback=c['cost']-b['cost'],
                         terminal_error=c['terminal_error'],slack_rate=c['slack_rate']))
    return pd.DataFrame(rows),b

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--output',default='results/mpc_v3'); ap.add_argument('--seeds',type=int,default=3); ap.add_argument('--hierarchical',action='store_true'); a=ap.parse_args()
    from pathlib import Path
    out=Path(a.output); out.mkdir(parents=True,exist_ok=True)
    load=load_inputs(); rows,b=value_experiment(load,tariff(len(load)),seeds=range(20261110,20261110+a.seeds))
    rows.to_csv(out/'value.csv',index=False); b['trace'].to_csv(out/'rolling_perfect.csv',index=False)
    from intervals import bootstrap
    pd.DataFrame([dict(metric=k,**dict(zip(('mean','lo','hi'),bootstrap(rows[k])))) for k in ('delta_reopt','delta_feedback')]).to_csv(out/'intervals.csv',index=False)
    print(rows.to_string(index=False))
    if a.hierarchical:
        h=rolling(load,tariff(len(load)),forecast_error=.1,hierarchical=True)
        h['trace'].to_csv(out/'hierarchical.csv',index=False)
        print('Hierarchical terminal error:',h['terminal_error'])
        if h['terminal_error']>=1e-3: raise SystemExit('hierarchical terminal closure failed')
    if rows.terminal_error.max()>=1e-3: raise SystemExit('terminal closure failed')
