"""Finite doctoral experiment suite. No sizing or additional algorithm expansion.
24 slots = full representative day at 1h; 8 slots = 2h software smoke only.
"""
import argparse
import json
import time
from dataclasses import replace
import numpy as np
import pandas as pd
from config import Config,ROOT,INPUTS,load_inputs,tariff
from physical_topology import architecture,Fault
from capability import standby_reference,envelope,deliverable_load
from foundation_experiments import example_contracts,save
from coupling import factorial
from research_verify import verify_capability
from outage import replay,recharge
from runctx import RunContext,snapshot_source
from energy_metrics import summarize as energy_summary
from standards import assess


def workloads(load):
    """Equal input energy proxies; not equal empirical delivered tokens/steps."""
    result={'mixed':load.copy()};total=load.sum(axis=1)
    for kind in ('train','infer','batch','deliv'):
        case=load*0.;case[kind]=total;result[kind]=case
    return result


def run(slots=24):
    if slots not in (8,24):raise ValueError('use 8 for software smoke, 24 for representative full day')
    started=time.perf_counter();cfg=replace(Config(),dt=1. if slots==24 else .25)
    load=load_inputs()
    load=load.groupby(np.arange(len(load))//4).mean().reset_index(drop=True) if slots==24 else load.iloc[:8].reset_index(drop=True)
    price=tariff(slots,cfg.dt) if slots==24 else np.array([372.,372.,649.9,991.9,991.9,649.9,372.,372.])
    run=RunContext(ROOT,prefix='thesis')
    for path in (INPUTS/'load_business_24h.csv',INPUTS/'degradation_calibration.csv',ROOT/'requirements-lock.txt',ROOT/'docs/parameter_provenance.csv',ROOT/'docs/standards/验收规范与证据矩阵.md',ROOT/'docs/standards/requirements.json'):
        run.freeze_input(path)
    snapshot_source(run.dir/'source');run.write_provenance();run.write_env()
    run.write_config(dict(suite='thesis',config=cfg.snapshot(),slots=slots,full_day=slots==24,empirical_sla=False,
        fixed_scope='4 architectures x 5 workload profiles; mixed factorial; two capability durations; fixed-service faults',
        capability_durations_h=[cfg.dt,2*cfg.dt],standard_compliance='BLOCKED'))
    checks=[];operations=[];capacities=[];factor_rows=[];outages=[];design=[];manifest=[]
    start=slots//3
    for name in (('N','N+1','2N+1','2(N+1)') if slots==24 else ('N+1','2N+1')):
        a=architecture(name)
        for label,lb in (workloads(load) if slots==24 else {'mixed':load}).items():
            contracts=example_contracts(lb,cfg)
            contracts=replace(contracts,inference=replace(contracts.inference,max_power=10.))
            reference=standby_reference(lb,price,a,cfg,contracts=contracts)
            key=name+'_'+label;checks.append(save(run.dir,key+'_reference',reference))
            operations.append(dict(arch=name,workload=label,cost=reference['cost'],input_proxy_mwh=float(lb.sum().sum()*cfg.dt),
                service_cost=reference['service_cost'],**energy_summary(reference['trace'],cfg)))
            for duration in (1,2):
                for mode in ('battery_only','joint'):
                    r=envelope(reference,lb,price,a,start,duration,cfg,contracts=contracts,mode=mode)
                    chk=verify_capability(reference,r,start,duration);chk['arch']=key;chk['policy']='event_attribution';checks.append(chk)
                    if not chk['pass'].all():raise AssertionError(chk[~chk['pass']].to_string())
                    witness_key=f'{key}_{mode}_{duration}'
                    checks.append(save(run.dir,witness_key,r.pop('witness')))
                    manifest.append(dict(reference=key+'_reference',witness=witness_key,start=start,duration_slots=duration,result=r))
                    capacities.append(dict(arch=name,workload=label,start_slot=start,**r))
            if label=='mixed':
                arms=factorial(lb,price,a,cfg,contracts,reference)
                for arm,r in arms.items():
                    checks.append(save(run.dir,key+'_'+arm,r))
                    factor_rows.append(dict(arch=name,arm=arm,**{k:r[k] for k in ('cost','lo','hi','energy_cost','demand_cost','deg_cost','capacity_rev','mileage_rev','service_cost')}))
                soc=[cfg.soc_max*u.energy*u.soh for u in a.units]
                for fault in a.faults('all')+[Fault('maintenance_path_A',paths=('A',))]:
                    for hours in (.25,.75):
                        # Replay from fixed standby state, preserving reference IT+AC.
                        out=replay(a,soc,reference['trace'].site.iloc[start:].to_numpy(),hours,cfg,fault)
                        rec=recharge(a,out['soc_end'],soc,reference['trace'].site.iloc[start+int(np.ceil(hours/cfg.dt)):].to_numpy(),cfg)
                        outages.append(dict(arch=name,duration_h=hours,**out,recovery=rec))
                    power=deliverable_load(a,soc,cfg.ride_h,fault,cfg.eta)
                    design.append(dict(arch=name,fault=fault.name,deliverable_mw=power,design_mw=a.design_load,meets_design=power>=a.design_load-1e-6))
            print(f'Validated {key}',flush=True)
    cap=pd.DataFrame(capacities);factor=pd.DataFrame(factor_rows)
    effects=[]
    for arch,d in factor.groupby('arch'):
        c=d.set_index('arm').cost
        effects.append(dict(arch=arch,coupling_reg_interaction=float(c.joint_no_reg+c.sequential_reg-c.sequential_no_reg-c.joint_reg)))
    # Nested feasible sets and event durations: empirical consistency checks,
    # not assertions that all architectures must pass every design failure.
    for _,d in cap.groupby(['arch','workload','duration_h']):
        x=d.set_index('mode');
        if x.loc['joint','power_hi']+1e-5<x.loc['battery_only','power_lo']:raise AssertionError('joint feasible-set inclusion')
    for _,d in cap.groupby(['arch','workload','mode']):
        d=d.sort_values('duration_h')
        if d.iloc[1].power_lo>d.iloc[0].power_hi+1e-5:raise AssertionError('duration monotonicity')
    for file,table in [('capability.csv',cap),('factorial.csv',factor),('interactions.csv',pd.DataFrame(effects)),('operation.csv',pd.DataFrame(operations)),('design_faults.csv',pd.DataFrame(design))]:table.to_csv(run.dir/file,index=False)
    (run.dir/'outages.json').write_text(json.dumps(outages,ensure_ascii=False,indent=2),encoding='utf-8')
    (run.dir/'capability_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    from independent_verify import verify_directory
    serialized=verify_directory(run.dir)
    if not serialized['pass'].all():raise AssertionError('serialized capability audit failed')
    chk=pd.concat(checks,ignore_index=True);chk.to_csv(run.dir/'checks_v3.csv',index=False)
    if not chk['pass'].all():raise AssertionError('independent checks failed')
    result=dict(software_status='PASS',standard_compliance='BLOCKED',empirical_sla=False,full_day=slots==24,
        checks=len(chk),serialized_checks=len(serialized),references=len(operations),capability_cases=len(cap),factorial_cells=len(factor),outage_cases=len(outages),
        seconds=time.perf_counter()-started,scope='fixed assets, synthetic contracts, deterministic representative-day mechanisms; no statistical population claim')
    standard_report=assess(json.loads((run.dir/'inputs/requirements.json').read_text(encoding='utf-8')))
    (run.dir/'standards_report.json').write_text(json.dumps(standard_report,ensure_ascii=False,indent=2),encoding='utf-8')
    result['standard_compliance']=standard_report['standards_status']
    (run.dir/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    run.seal()
    from verify import verify_run
    if verify_run(run.dir):raise AssertionError('seal failed')
    run.publish([p.name for p in run.dir.iterdir() if p.is_file()],ROOT/'results/thesis')
    print(json.dumps(dict(run=str(run.dir),**result),ensure_ascii=False),flush=True)
    return run.dir


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--slots',type=int,choices=(8,24),default=24);args=ap.parse_args();run(args.slots)
