"""Topology × service-contract research, independent of the frozen V3 baseline.

Run: python sim/foundation_experiments.py --slots 8
Default is a 24-hour hourly representative day, not a second-resolution SLA trace.
"""
import argparse
import json
from dataclasses import replace
import numpy as np
import pandas as pd
from config import Config,ROOT,load_inputs,tariff,INPUTS,RAW_DATA
from dispatch import solve
from physical_topology import architecture,Fault
from service_contracts import ServiceContracts,Job,InferenceSLO
from capability import deliverable_load,envelope,standby_reference
from independent_verify import verify_trace
from research_verify import verify_research
from runctx import RunContext,snapshot_source

def example_contracts(load,cfg):
    """Explicit scenario assumptions; workload energy only is derived from input."""
    n=len(load);jobs=[]
    for kind in ('train','batch','deliv'):
        # Hard deadline for training/deliverables; batch may miss its deadline at
        # a stated price. Separate penalty experiments exercise that tradeoff.
        work=float(load[kind].sum()*cfg.dt)
        jobs.append(Job(kind,kind,0,n,work,max(1.,2*float(load[kind].max())),
                        min_power=.1 if kind=='train' else 0.,
                        min_run=min(2,n) if kind=='train' else 1,
                        restart_energy=.02 if kind=='train' else 0.,
                        soft_deadline=kind=='batch',unmet_price=100000.))
    arrivals=tuple(load['infer'].to_numpy()*1e6/1000.)
    slo=InferenceSLO(arrivals,response_s=.2,violation_probability=.01,joules_per_request=1000.,idle_mw=.02,max_power=2.)
    return ServiceContracts(tuple(jobs),slo)

def save(out,name,result):
    result['trace'].to_csv(out/f'trace_{name}.csv',index=False,float_format='%.12g')
    result['physical_trace'].to_csv(out/f'physical_{name}.csv',index=False,float_format='%.12g')
    if 'service_trace' in result:result['service_trace'].to_csv(out/f'service_{name}.csv',index=False,float_format='%.12g')
    meta={k:v for k,v in result.items() if k not in ('trace','physical_trace','service_trace')}
    (out/f'trace_{name}.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    # Audit serialized numbers, not only the in-memory solution.
    tr=pd.read_csv(out/f'trace_{name}.csv');ph=pd.read_csv(out/f'physical_{name}.csv')
    se=pd.read_csv(out/f'service_{name}.csv') if 'service_trace' in result else None
    checks=pd.concat([verify_trace(tr,meta),verify_research(tr,meta,ph,se)],ignore_index=True)
    checks['arch']=name;checks['policy']='physical_service'
    if not checks['pass'].all():raise AssertionError(checks[~checks['pass']].to_string(index=False))
    return checks

def run(slots=24,reference_mode='standby',publish=True):
    if slots not in (8,24,96):raise ValueError('supported grids: 8 demo slots, 24 hourly, 96 quarter-hour')
    cfg=replace(Config(),dt=1. if slots==24 else .25)
    load=load_inputs()
    if slots==24:load=load.groupby(np.arange(len(load))//4).mean().reset_index(drop=True)
    else:load=load.iloc[:slots].reset_index(drop=True)
    # A documented tariff stress for the short demo spans peak and valley.
    price=tariff(slots,cfg.dt) if slots!=8 else np.array([372.,372.,649.9,991.9,991.9,649.9,372.,372.])
    contracts=example_contracts(load,cfg)
    run=RunContext(ROOT,prefix='foundations')
    for path in (INPUTS/'load_business_24h.csv',INPUTS/'degradation_calibration.csv',ROOT/'docs/parameter_provenance.csv',ROOT/'requirements-lock.txt'):
        run.freeze_input(path)
    snapshot_source(run.dir/'source');run.write_provenance();run.write_env()
    run.write_config(dict(slots=slots,config=cfg.snapshot(),contracts=contracts.snapshot(),
                          reference_mode=reference_mode,
                          scope='one grid outage plus one UPS-unit outage; path failures evaluated separately',
                          full_day=slots in (24,96),empirical_sla=False))
    checks=[];operation=[];capabilities=[];faults=[]
    for name in ('N','N+1','2N+1','2(N+1)'):
        a=architecture(name)
        if reference_mode=='standby':reference=standby_reference(load,price,a,cfg,contracts=contracts)
        elif reference_mode=='economic':reference=solve(load,price,len(a.units),cfg,physical_topology=a,contingency_scope='module',service_contracts=contracts,reg=False,flex=False)
        else:raise ValueError('invalid reference mode')
        checks.append(save(run.dir,name,reference))
        operation.append(dict(arch=name,modules=len(a.units),cost=reference['cost'],cost_lo=reference['lo'],cost_hi=reference['hi'],
                              useful_work=sum(j.work for j in contracts.jobs),electric_mwh=cfg.dt*reference['trace'].it.sum(),
                              contract_penalty=reference.get('service_cost',0.)))
        soc=[cfg.soc_max*u.energy*u.soh for u in a.units]
        for state in a.faults('all'):
            power=deliverable_load(a,soc,cfg.ride_h,state,cfg.eta)
            faults.append(dict(arch=name,fault=state.name,critical_mw=power,design_mw=a.design_load,
                               meets_design=power>=a.design_load-1e-6,ride_h=cfg.ride_h))
        for duration in (1,2):
            for mode in ('battery_only','joint'):
                r=envelope(reference,load,price,a,2,duration,cfg,contracts=contracts,mode=mode)
                checks.append(save(run.dir,f'{name}_{mode}_{duration}',r.pop('witness')))
                capabilities.append(dict(arch=name,start_slot=2,**r))
    pd.DataFrame(operation).to_csv(run.dir/'operation.csv',index=False)
    pd.DataFrame(capabilities).to_csv(run.dir/'capability.csv',index=False)
    pd.DataFrame(faults).to_csv(run.dir/'fault_design_audit.csv',index=False)
    pd.concat(checks,ignore_index=True).to_csv(run.dir/'checks_v3.csv',index=False)
    run.seal()
    if publish:run.publish([p.name for p in run.dir.iterdir() if p.is_file()],ROOT/'results/foundations')
    from verify import verify_run
    if verify_run(run.dir):raise AssertionError('foundation seal failed')
    print(f'Foundations: {slots} slots, {sum(len(c) for c in checks)} checks -> {run.dir}',flush=True)
    return run.dir

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--slots',type=int,default=24);ap.add_argument('--reference',choices=['standby','economic'],default='standby');ap.add_argument('--no-publish',action='store_true');args=ap.parse_args();run(args.slots,args.reference,not args.no_publish)
