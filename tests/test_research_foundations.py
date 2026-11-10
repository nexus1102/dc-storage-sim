from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from config import Config,load_inputs,tariff
from physical_topology import architecture,Topology,Unit,PowerPath,LoadGroup,Fault
from capability import deliverable_load,envelope
from dispatch import solve
from service_contracts import Job,InferenceSLO,ServiceContracts
from independent_verify import verify_trace
from research_verify import verify_research

def test_full_design_module_and_path_failures():
    # Full design-load guarantee differs from serving today's lighter actual load.
    for name,expected in [('N',8.),('N+1',10.),('2N+1',10.),('2(N+1)',10.)]:
        a=architecture(name)
        soc=[.475]*len(a.units)
        power=deliverable_load(a,soc,.05,Fault('module',units=(a.units[0].name,)))
        assert power==pytest.approx(expected,abs=1e-6)
    a=architecture('N+1')
    assert deliverable_load(a,[.475]*6,.05,Fault('path',paths=('A',)))==0.
    a=architecture('2(N+1)')
    assert deliverable_load(a,[.475]*12,.05,Fault('path',paths=('A',)))==pytest.approx(10.)

def test_equal_capacity_different_connections():
    # Identical energy and power ratings, but a path outage strands half the battery.
    isolated=architecture('2N')
    pooled=replace(isolated,units=tuple(replace(u,paths=('A','B')) for u in isolated.units))
    soc=[.475]*10; fault=Fault('path',paths=('A',))
    a=deliverable_load(isolated,soc,.5,fault)
    b=deliverable_load(pooled,soc,.5,fault)
    assert b>a+1 and isolated.energy==pooled.energy

def test_same_total_soc_different_location():
    a=architecture('2N');fault=Fault('path',paths=('A',))
    soc1=[.475]*5+[.05]*5;soc2=[.05]*5+[.475]*5
    assert sum(soc1)==pytest.approx(sum(soc2))
    assert deliverable_load(a,soc1,.25,fault)<deliverable_load(a,soc2,.25,fault)

def test_power_redundancy_does_not_imply_ride_energy_guarantee():
    a=architecture('N+1');fault=Fault('module',units=('A0',))
    # Five surviving 2-MW units satisfy the 10-MW power check, but usable SOC
    # and efficiency reduce 15-minute deliverable energy below 2.5 MWh.
    assert 5*2.==a.design_load
    assert deliverable_load(a,[.475]*6,.25,fault)==pytest.approx(9.025)

def test_shared_spare_cannot_feed_two_disconnected_racks():
    a=Topology('switch', (Unit('spare',('A','B'),energy=1.,power=2.),),
               (PowerPath('A',2.),PowerPath('B',2.)),
               (LoadGroup('a',('A',),.5),LoadGroup('b',('B',),.5)))
    # An unswitched fractional-flow relaxation would incorrectly return >0 MW.
    assert deliverable_load(a,[1.],.25,Fault('normal'))==pytest.approx(0.,abs=1e-7)

@pytest.fixture(scope='module')
def coupled():
    load=load_inputs().iloc[:8].reset_index(drop=True)
    cfg=replace(Config(),thermal_c=1.,ride_h=.25)
    jobs=ServiceContracts((Job('train','train',0,8,2.,2.,min_power=.2,min_run=2,restart_energy=.01),
                           Job('batch','batch',1,6,.1,.5),Job('delivery','deliv',0,7,.5,1.)),
                          InferenceSLO(tuple([50.]*8),max_power=.5))
    a=architecture('N+1')
    result=solve(load,tariff(8),6,cfg,physical_topology=a,service_contracts=jobs,reg=False)
    return load,cfg,jobs,a,result

def test_coupled_component_and_sla_audit(coupled):
    _,_,_,_,r=coupled
    assert verify_trace(r['trace'],r)['pass'].all()
    checks=verify_research(r['trace'],r,r['physical_trace'],r['service_trace'])
    assert checks['pass'].all(),checks[~checks['pass']].to_string()

def test_csv_directory_verifier_includes_component_constraints(coupled,tmp_path):
    from foundation_experiments import save
    from independent_verify import verify_directory
    result=coupled[-1];save(tmp_path,'case',result)
    checks=verify_directory(tmp_path)
    assert checks['pass'].all() and checks['check'].str.startswith('fault_service_').any()
    trace=pd.read_csv(tmp_path/'physical_case.csv')
    trace.loc[trace.kind=='routes','value']+=.1
    trace.to_csv(tmp_path/'physical_case.csv',index=False)
    assert not verify_directory(tmp_path)['pass'].all()

def test_audit_detects_fault_energy_and_deadline_tampering(coupled):
    _,_,_,_,r=coupled
    tr=r['physical_trace'].copy();tr.loc[tr.kind=='routes','value']+=1.
    assert not verify_research(r['trace'],r,tr,r['service_trace'])['pass'].all()
    jobs=r['service_trace'].copy();jobs.loc[(jobs.job=='delivery')&(jobs.t==7),'work_mw']=.5
    assert not verify_research(r['trace'],r,r['physical_trace'],jobs)['pass'].all()

def test_slo_tightening_increases_reserved_service_power():
    lb=load_inputs().iloc[:4]
    def run(delay):
        s=ServiceContracts((),InferenceSLO(tuple([50.]*4),response_s=delay,max_power=2.))
        return solve(lb,tariff(4),6,service_contracts=s,reg=False,thermal=False)
    assert run(.1)['trace'].service_infer.mean()>run(.5)['trace'].service_infer.mean()

def test_training_overhead_and_hard_vs_soft_deadline():
    lb=load_inputs().iloc[:4]
    hard=ServiceContracts((Job('impossible','deliv',0,1,2.,.1),))
    with pytest.raises(RuntimeError,match='infeasible'):solve(lb,tariff(4),6,service_contracts=hard)
    soft=ServiceContracts((replace(hard.jobs[0],soft_deadline=True),))
    r=solve(lb,tariff(4),6,service_contracts=soft)
    assert r['service_cost']>100000 and r['service_trace'].unmet.max()>1.

def test_capability_power_duration_and_fixed_state(coupled):
    load,cfg,jobs,a,ref=coupled
    short=envelope(ref,load,tariff(8),a,2,1,cfg,contracts=jobs)
    long=envelope(ref,load,tariff(8),a,2,2,cfg,contracts=jobs)
    assert short['power_mw']+1e-5>=long['power_mw']
    assert short['power_lo']<=short['power_mw']<=short['power_hi']
    witness=short['witness']
    assert verify_research(witness['trace'],witness,witness['physical_trace'],witness['service_trace'])['pass'].all()
    # The capacity witness cannot rewrite executed useful-work progress.
    for name in [j.name for j in jobs.jobs]:
        a=ref['service_trace'].query('job == @name and t < 2').sort_values('t')
        b=witness['service_trace'].query('job == @name and t < 2').sort_values('t')
        assert np.allclose(a.work_mw,b.work_mw)
        assert b.unmet.iloc[0]<=a.unmet.iloc[0]+1e-6

def test_invalid_topology_and_service_inputs():
    with pytest.raises(ValueError):architecture('2N+1',n=0)
    with pytest.raises(ValueError):ServiceContracts((Job('x','train',2,1,1.,1.),)).validate(4,.25)

def test_backup_baseline_has_no_free_arbitrage():
    from capability import standby_reference
    lb=load_inputs().iloc[:4];a=architecture('N+1')
    r=standby_reference(lb,tariff(4),a)
    assert np.allclose(r['trace'].discharge,0.) and np.allclose(r['trace'].charge,0.)
    for name,d in r['physical_trace'].query("kind == 'soc'").groupby('key'):
        assert np.allclose(d.value,.475)
