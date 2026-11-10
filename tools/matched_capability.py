"""Finite matched-reference audit: isolate assets from endogenous baseline shape."""
import json
import sys
from pathlib import Path
from dataclasses import replace
import linopy
import numpy as np
import pandas as pd
import xarray as xr
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'sim'))
from config import ROOT,Config,INPUTS,load_inputs,tariff
from physical_topology import architecture
from foundation_experiments import example_contracts,save
from capability import standby_reference,envelope
from dispatch import build_block,optimize,extract
from research_verify import verify_capability
from independent_verify import verify_directory
from runctx import RunContext,snapshot_source
from verify import verify_run


def run():
    cfg=replace(Config(),dt=1.);load=load_inputs().groupby(np.arange(96)//4).mean().reset_index(drop=True);price=tariff(24,1.)
    contracts=example_contracts(load,cfg);contracts=replace(contracts,inference=replace(contracts.inference,max_power=10.))
    common=standby_reference(load,price,architecture('N'),cfg,contracts=contracts)
    context=RunContext(ROOT,prefix='matched')
    for path in (INPUTS/'load_business_24h.csv',INPUTS/'degradation_calibration.csv',Path(__file__).resolve()):context.freeze_input(path)
    snapshot_source(context.dir/'source');context.write_provenance();context.write_env()
    context.write_config(dict(suite='matched',config=cfg.snapshot(),contracts=contracts.snapshot(),scope='four architectures; identical full baseline IT/cooling/useful-work; SOCmax per unit; start=8; two durations; two modes'))
    checks=[];records=[];manifest=[]
    for name in ('N','N+1','2N+1','2(N+1)'):
        a=architecture(name);m=linopy.Model();fixed={k:common['trace'][k].to_numpy() for k in ('it','ac')}
        b=build_block(m,load,price,len(a.units),cfg,physical_topology=a,service_contracts=contracts,reg=False,flex=False,fixed_operation=fixed)
        m.add_constraints(b.variables['charge']==0,name='no_charge');m.add_constraints(b.variables['discharge']==0,name='no_discharge')
        for unit in a.units:m.add_constraints(b.physical['soc'][unit.name]==cfg.soc_max*unit.energy*unit.soh,name='standby_'+unit.name)
        for key,entry in b.services['jobs'].items():
            history=common['service_trace'].query('job == @key').sort_values('t')
            for variable,column in (('work','work_mw'),('on','on'),('start','start')):
                if entry[variable] is not None:
                    values=xr.DataArray(history[column].to_numpy(),coords=[pd.Index(range(24),name='t')])
                    m.add_constraints(entry[variable]==values,name='common_'+key+'_'+variable)
            m.add_constraints(entry['unmet']==float(history.unmet.iloc[0]),name='common_unmet_'+key)
        m.add_objective(b.cost);bounds=optimize(m);reference=extract(b);reference.update(modules=len(a.units),lo=bounds.lo,hi=bounds.hi)
        for key in ('grid','it','ac','cold'):
            if not np.allclose(reference['trace'][key],common['trace'][key],atol=1e-6,rtol=0):raise AssertionError('unequal baseline '+key)
        sa=reference['service_trace'].sort_values(['job','t']);sb=common['service_trace'].sort_values(['job','t'])
        if not np.allclose(sa.drop(columns=['job','kind']).to_numpy(),sb.drop(columns=['job','kind']).to_numpy(),atol=1e-6,rtol=0):raise AssertionError('unequal baseline useful-work history')
        checks.append(save(context.dir,name+'_reference',reference))
        for duration in (1,2):
            for mode in ('battery_only','joint'):
                result=envelope(reference,load,price,a,8,duration,cfg,contracts=contracts,mode=mode)
                chk=verify_capability(reference,result,8,duration);chk['arch']=name;chk['policy']='matched_event';checks.append(chk)
                key=f'{name}_{mode}_{duration}';checks.append(save(context.dir,key,result.pop('witness')))
                manifest.append(dict(reference=name+'_reference',witness=key,start=8,duration_slots=duration,result=result))
                records.append(dict(arch=name,**result))
        print('Matched baseline validated: '+name,flush=True)
    pd.DataFrame(records).to_csv(context.dir/'matched_capability.csv',index=False)
    (context.dir/'capability_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    checks=pd.concat(checks,ignore_index=True)
    if not checks['pass'].all():raise AssertionError('matched checks failed')
    checks.to_csv(context.dir/'checks_v3.csv',index=False)
    serialized=verify_directory(context.dir)
    if not serialized['pass'].all():raise AssertionError('matched serialized checks failed')
    (context.dir/'acceptance.json').write_text(json.dumps(dict(software_status='PASS',standard_compliance='BLOCKED',matched_reference=True,capacity_points=len(records),checks=len(serialized)),indent=2),encoding='utf-8')
    context.seal()
    if verify_run(context.dir):raise AssertionError('matched seal failed')
    context.publish([p.name for p in context.dir.iterdir() if p.is_file()],ROOT/'results/matched')
    print(str(context.dir),flush=True)
    return context.dir

if __name__=='__main__':run()
