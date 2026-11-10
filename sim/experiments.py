"""Staged experiments and publication artifacts. Failures are never published."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from config import Config,ROOT,INPUTS,RAW_DATA,BASELINE,KEYS,load_inputs,tariff
from dispatch import solve
from intervals import Interval,bootstrap
from independent_verify import verify_trace
from provenance import table as provenance_table
from runctx import RunContext,sha256,snapshot_source

COUNTS={'N':5,'N+1':6,'2N':10,'2N+1':11,'2(N+1)':12}

def save_result(out,name,r):
    r['trace'].to_csv(out/f'trace_{name}.csv',index=False,float_format='%.12g')
    meta={k:v for k,v in r.items() if k!='trace'}
    (out/f'trace_{name}.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    chk=verify_trace(pd.read_csv(out/f'trace_{name}.csv'),meta)
    if not chk['pass'].all(): raise AssertionError(chk[~chk['pass']].to_string(index=False))
    chk['arch']=name; chk['policy']='v3'
    return chk

def summary(r):
    return {k:v for k,v in r.items() if k not in ('trace','config','options')}

def base_cost(load,price,cfg,thermal=True):
    it=load.sum(axis=1).to_numpy()
    ac=(it+cfg.thermal_ua*(cfg.thermal_ambient-(cfg.thermal_tmax-.5*cfg.thermal_range)))/cfg.thermal_cop if thermal else 0.
    g=it+ac
    return float(cfg.dt*np.dot(g,price)+cfg.demand_rate*np.max(g))

def stage0(out,load,price,cfg,n_scen):
    rows=[]; checks=[]; base=base_cost(load,price,cfg)
    for name,n in COUNTS.items():
        r=solve(load,price,n,cfg)
        checks.append(save_result(out,name,r))
        rows.append(dict(arch=name,policy='v3',n1=n>=6,**summary(r),saving=base-r['cost'],
                         saving_lo=base-r['hi'],saving_hi=base-r['lo'],
                         dispatchable=float((cfg.soc_max*r['energy']-r['trace'].floor).mean())))
    df=pd.DataFrame(rows); df.to_csv(out/'results_v3.csv',index=False,float_format='%.10f')
    sensitivity=[]
    for _,row in provenance_table().query("source_type == '假设'").iterrows():
        for bound in ('low','high'):
            value=row[bound]
            if row.parameter=='years': value=int(value)
            options={}
            if row.parameter.startswith('business_'):
                _,key,attr=row.parameter.split('_',2)
                c=cfg; options={'business_override':{key:{attr:value}}}
            else: c=replace(cfg,**{row.parameter:value})
            try:
                r=solve(load,price,6,c,**options)
                # Economic-only parameters affect annualized total, not dispatch cost.
                from economics import crf
                annual=r['cost']*365+3000*c.capex_kwh*crf(c.discount,c.years)
                sensitivity.append(dict(parameter=row.parameter,bound=bound,value=value,cost=r['cost'],annual_cost=annual,feasible=True))
            except RuntimeError as exc:
                if 'infeasible' not in str(exc): raise
                sensitivity.append(dict(parameter=row.parameter,bound=bound,value=value,cost=np.nan,annual_cost=np.nan,feasible=False))
    pd.DataFrame(sensitivity).to_csv(out/'parameter_sensitivity.csv',index=False)
    rides=[]
    for minute in (3,10,15,30,45):
        for name,n in COUNTS.items():
            c=replace(cfg,ride_h=minute/60)
            try:
                r=solve(load,price,n,c,policy='worst')
                rides.append(dict(minutes=minute,arch=name,dispatchable=float((c.soc_max*r['energy']-r['trace'].floor).mean()),saving=base_cost(load,price,c)-r['cost'],feasible=True))
            except RuntimeError as exc:
                if 'infeasible' not in str(exc): raise
                rides.append(dict(minutes=minute,arch=name,dispatchable=0.,saving=np.nan,feasible=False))
    ride=pd.DataFrame(rides); ride.to_csv(out/'ride_sensitivity.csv',index=False)
    fig,axes=plt.subplots(1,2,figsize=(10,4))
    for name in COUNTS:
        d=ride[ride.arch==name]
        axes[0].plot(d.minutes,d.dispatchable,'o-',label=name)
        axes[1].plot(d.minutes,d.saving,'o-')
    axes[0].set(xlabel='Ride-through (min)',ylabel='Dispatchable MWh'); axes[0].legend()
    axes[1].set(xlabel='Ride-through (min)',ylabel='Saving CNY/day')
    fig.tight_layout(); fig.savefig(out/'ride_sensitivity.png',dpi=140); plt.close(fig)
    return checks

def stage1(out,load,price,cfg,n_scen):
    from stochastic import scenarios
    from jobs import decompose
    from business import BUSINESS
    from business_evidence import summarize
    summarize(RAW_DATA).to_csv(out/'business_trace_evidence.csv',index=False)
    checks=[]; factorial=[]; jobs=[]; thermal_rows=[]
    for i,(lb,pr) in enumerate(scenarios(load,price,n_scen,cfg.seed)):
        cell={}
        for flex,reg,label in ((False,False,'A'),(True,False,'B'),(False,True,'C'),(True,True,'D')):
            r=solve(lb,pr,6,cfg,flex=flex,reg=reg)
            cell[label]=Interval(r['lo'],r['hi'])
            if i==0: checks.append(save_result(out,'factor_'+label,r))
            if label=='D':
                for key,spec in BUSINESS.items():
                    decomp=decompose(r['trace']['defer_'+key],r['trace']['recover_'+key],spec.window_h*cfg.window_scale,cfg.dt)
                    jobs.append(dict(scenario=i,business=key,l1=decomp['l1'],passed=decomp['passed']))
        did=cell['B']+cell['C']-cell['A']-cell['D']
        f=((cell['A']-cell['B'])+(cell['C']-cell['D']))
        g=((cell['A']-cell['C'])+(cell['B']-cell['D']))
        factorial.append(dict(scenario=i,interaction=(did.lo+did.hi)/2,lo=did.lo,hi=did.hi,
                              flex_main=(f.lo+f.hi)/4,reg_main=(g.lo+g.hi)/4))
    fdf=pd.DataFrame(factorial); fdf.to_csv(out/'factorial.csv',index=False)
    effects=[]
    for metric in ('interaction','flex_main','reg_main'):
        mean,lo,hi=bootstrap(fdf[metric]); effects.append(dict(effect=metric,mean=mean,lo=lo,hi=hi,positive=lo>0))
    pd.DataFrame(effects).to_csv(out/'effects.csv',index=False)
    jdf=pd.DataFrame(jobs); jdf.to_csv(out/'job_decomposition.csv',index=False)
    if jdf.groupby('scenario').passed.all().mean()<=.95: raise AssertionError('job decomposition acceptance failed')
    for thermal,policy,deg in ((False,'dynamic','sos2'),(True,'static','sos2'),(True,'dynamic','legacy'),(True,'dynamic','sos2')):
        r=solve(load,price,6,cfg,thermal=thermal,policy=policy,degradation=deg)
        checks.append(save_result(out,f'ablation_{thermal}_{policy}_{deg}',r))
        tr=r['trace']; precool=(tr.ac-(tr.it+cfg.thermal_ua*(cfg.thermal_ambient-(cfg.thermal_tmax-.5*cfg.thermal_range)))/cfg.thermal_cop)
        thermal_rows.append(dict(thermal=thermal,policy=policy,degradation=deg,cost=r['cost'],
                                 cooling_price_corr=float(np.corrcoef(tr.ac,tr.price)[0,1]) if thermal else np.nan,
                                 precool_mwh=float(np.maximum(precool,0).sum()*cfg.dt),
                                 queue_peak=float(tr[[f'queue_{k}' for k in KEYS]].max().max()),
                                 reg_capacity_rev=r['capacity_rev'],reg_mileage_rev=r['mileage_rev']))
    pd.DataFrame(thermal_rows).to_csv(out/'coupling_ablation.csv',index=False)
    return checks

def stage2(out,load,price,cfg,n_scen):
    from sizing import candidates,select,epsilon_frontier,weighted_sum,legacy_anchors
    from economics import lifetime
    d=candidates(load,price,cfg=cfg); d.to_csv(out/'sizing_candidates.csv',index=False)
    chosen=select(d); (out/'sizing_optimum.json').write_text(json.dumps(chosen,indent=2),encoding='utf-8')
    # For each epsilon all five canonical architectures are re-dispatched.
    frontier=epsilon_frontier(load,price,list(COUNTS.values()),cfg)
    frontier.to_csv(out/'pareto.csv',index=False)
    weighted_sum(d).to_csv(out/'weighted_sum.csv',index=False)
    rows=[]
    for n in COUNTS.values():
        for coupled in (False,True):
            r=lifetime(load,price,n,base_cost(load,price,cfg),cfg,coupled=coupled)
            r.pop('cashflows').to_csv(out/f'cashflow_{n}_{coupled}.csv',index=False)
            rows.append(dict(modules=n,coupled=coupled,**r))
    pd.DataFrame(rows).to_csv(out/'lifetime.csv',index=False)
    fig,ax=plt.subplots(); ax.plot(frontier.eens,frontier.annual_cost,'o-'); ax.set(xlabel='Stress-event EENS (MWh/event)',ylabel='Annualized CNY/year'); fig.tight_layout(); fig.savefig(out/'pareto.png'); plt.close(fig)
    # Historical data and kernels are retained so a changed scientific model is
    # never required to produce the same cost as the model it corrects.
    anchors=legacy_anchors(load); anchors.to_csv(out/'legacy_sizing_anchor.csv',index=False)
    if not anchors.passed.all(): raise AssertionError('historical v2 anchor drift')
    return [pd.DataFrame([dict(arch='planning',policy='v3',check='master_gap',residual=chosen['master_gap'],tol=1e-4,**{'pass':chosen['master_gap']<1e-4}),
                         dict(arch='planning',policy='v3',check='pareto_monotonic',residual=float(max(0.,frontier.eens.diff().max() or 0.)),tol=1e-9,**{'pass':bool((frontier.eens.diff().dropna()<=1e-9).all())})])]

def stage3(out,load,price,cfg,n_scen):
    from stochastic import solve_two_stage,scenarios,evaluate
    from dro import worst_probabilities
    # Training and evaluation seeds disjoint. The exact same OOS set for every arm.
    training=scenarios(load,price,4,cfg.seed)
    test=scenarios(load,price,n_scen,cfg.seed+1000,band=.2)
    arms={'deterministic':[(load,price)],'stochastic':training,'dro':training,
          'conservative':[(load*1.2,price*.8)]}
    results=[]; certificates=[]; checks=[]
    for name,cases in arms.items():
        r=solve_two_stage(cases,cfg=cfg,radius=.4 if name=='dro' else 0.)
        certificates.append(dict(arm=name,cost=r['cost'],lo=r['lo'],hi=r['hi'],nonanticipation=r['nonanticipation']))
        pd.DataFrame(r['first']).to_csv(out/f'bids_{name}.csv',index=False)
        for i,s in enumerate(r['scenarios']):
            s['modules']=6; checks.append(save_result(out,f'{name}_{i}',s))
        evaluated=evaluate(r['first'],test,cfg=cfg); evaluated['arm']=name; results.append(evaluated)
    all_results=pd.concat(results,ignore_index=True); all_results.to_csv(out/'oos.csv',index=False)
    pd.DataFrame(certificates).to_csv(out/'nonanticipation.csv',index=False)
    wide=all_results.pivot(index='scenario',columns='arm',values='cost')
    comparisons=[]
    for arm in ('stochastic','dro','conservative'):
        mean,lo,hi=bootstrap(wide[arm]-wide.deterministic)
        comparisons.append(dict(arm=arm,mean=mean,lo=lo,hi=hi,cheaper=hi<0))
    pd.DataFrame(comparisons).to_csv(out/'oos_paired.csv',index=False)
    wc=[]
    scenario_costs=[r['cost'] for r in solve_two_stage(training,cfg=cfg)['scenarios']]
    for radius in (0.,.2,.4,.8,1.2):
        r=worst_probabilities(scenario_costs,radius=radius)
        wc.append(dict(radius=radius,cost=r['cost'],l1=r['l1'],probabilities=json.dumps(r['probabilities'].tolist())))
    pd.DataFrame(wc).to_csv(out/'dro_radius.csv',index=False)
    pd.DataFrame([dict(scenario=i,load_multiplier=float(lb.sum().sum()/load.sum().sum()),price_multiplier=float(pr.mean()/price.mean())) for i,(lb,pr) in enumerate(test)]).to_csv(out/'oos_scenarios.csv',index=False)
    return checks

def run_stage(stage,n_scen=40,input_path=None):
    started=time.perf_counter(); cfg=Config()
    run=RunContext(ROOT,prefix=f'v3s{stage}')
    frozen=run.freeze_input(Path(input_path or INPUTS/'load_business_24h.csv'))
    run.freeze_input(INPUTS/'degradation_calibration.csv')
    run.freeze_input(ROOT/'docs/parameter_provenance.csv')
    run.freeze_input(ROOT/'requirements-lock.txt')
    if stage==1:
        for name in ('openb_pod_list_default.csv','openb_pod_list_cpu050.csv'): run.freeze_input(RAW_DATA/name)
    if stage==2: run.freeze_input(BASELINE/'v2_reference.csv')
    snapshot_source(run.dir/'source')
    load=load_inputs(frozen); price=tariff(len(load),cfg.dt)
    run.write_config(dict(stage=stage,n_scen=n_scen,**cfg.snapshot()))
    run.write_env(); run.write_provenance()
    checks=globals()[f'stage{stage}'](run.dir,load,price,cfg,n_scen)
    if not checks:
        raise AssertionError('stage did not emit acceptance checks')
    pd.concat(checks,ignore_index=True).to_csv(run.dir/'checks_v3.csv',index=False)
    elapsed=time.perf_counter()-started
    (run.dir/'execution.json').write_text(json.dumps(dict(elapsed_seconds=elapsed,within_10min=elapsed<600),indent=2),encoding='utf-8')
    run.seal()
    dest=ROOT/'results'/f'v3_stage{stage}'
    run.publish([p.name for p in run.dir.iterdir() if p.is_file()],dest)
    from verify import verify_run
    if verify_run(run.dir): raise AssertionError('seal verification failed')
    print(f'Stage {stage}: {elapsed:.1f}s -> {run.dir}',flush=True)
    return run.dir

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--stage',choices=['0','1','2','3','all'],default='all')
    ap.add_argument('--scenarios',type=int,default=40); ap.add_argument('--input'); a=ap.parse_args()
    if a.scenarios<1: ap.error('--scenarios must be positive')
    for stage in range(4) if a.stage=='all' else [int(a.stage)]: run_stage(stage,a.scenarios,a.input)

if __name__=='__main__': main()
