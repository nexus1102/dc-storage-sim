from dataclasses import replace
import linopy
import numpy as np
import pandas as pd
import pytest
from config import Config,load_inputs,tariff
from dispatch import solve,optimize,build_block,extract
from degradation import add_cost
from independent_verify import verify_trace


def test_single_cycle_recovers_calibrated_asset_loss(tmp_path):
    # Independent capital loss, not a reconstruction of add_cost's formula.
    capex=800000.; fade=.0001; depth=.3
    path=tmp_path/'calibration.csv'
    pd.DataFrame({'DoD':[.3,.6,.9], 'cost_yuan_per_MWh':[capex*fade/(2*depth)]*3}).to_csv(path,index=False)
    for mode in ('sos2','shallow'):
        m=linopy.Model(); half=m.add_variables(lower=depth,upper=depth,name='known_cycle')
        m.add_objective(add_cost(m,half,1.,'cost',mode=mode,path=path,accounting_version=4))
        optimize(m)
        assert float(m.objective.value)==pytest.approx(capex*fade)


def test_corrected_cost_independently_audited():
    load=load_inputs().iloc[:8]; price=tariff(8)
    r=solve(load,price,cfg=replace(Config(),accounting_version=4),reg=False)
    assert verify_trace(r['trace'],r)['pass'].all()
    tamper=dict(r,deg_cost=r['deg_cost']/2)
    assert not verify_trace(r['trace'],tamper)['pass'].all()


@pytest.mark.parametrize('eta',[1.,.95])
@pytest.mark.parametrize('up,dn',[(.5,.5),(1.,0.),(0.,1.)])
def test_known_activation_energy_and_meter(eta,up,dn):
    # One hour at 0.4 MW: independent energy balance on a known dispatch.
    lb=pd.DataFrame({'train':[0.]*4,'infer':[2.]*4,'batch':[0.]*4,'deliv':[0.]*4})
    cfg=replace(Config(),eta=eta)
    m=linopy.Model();b=build_block(m,lb,np.full(4,100.),6,cfg,thermal=False,flex=False,
        initial={'soc':2.,'cold':.5},activation_profile={'up':up,'dn':dn})
    for key,value in [('charge',0.),('discharge',0.),('reg_up',.4),('reg_dn',.4)]:
        m.add_constraints(b.variables[key]==value,name='fixture_'+key)
    m.add_objective(b.cost);optimize(m);r=extract(b);r['modules']=6
    assert r['trace'].soc.iloc[-1]==pytest.approx(2.+eta*.4*dn-.4*up/eta)
    assert r['energy_cost']==pytest.approx(100.*(2.+.4*dn-.4*up))
    assert r['throughput']==pytest.approx(.4*(up+dn))
    assert verify_trace(r['trace'],r)['pass'].all()
    broken=r['trace'].copy();broken.loc[0,'agc_charge_mwh']+=.01
    assert not verify_trace(broken,r)['pass'].all()


def test_unit_agc_closure_and_biased_scenario():
    from physical_topology import architecture
    from research_verify import verify_research
    from stochastic import solve_two_stage,evaluate
    lb=load_inputs().iloc[:4];price=tariff(4);a=architecture('N+1')
    m=linopy.Model();b=build_block(m,lb,price,len(a.units),physical_topology=a,activation_profile={'up':1.,'dn':0.})
    m.add_constraints(b.variables['reg_up'][0]>=.01,name='exercise_up_activation')
    m.add_objective(b.cost);optimize(m);r=extract(b);r['modules']=len(a.units)
    assert r['trace'].agc_discharge_mwh.sum()>0
    assert verify_trace(r['trace'],r)['pass'].all()
    assert verify_research(r['trace'],r,r['physical_trace'])['pass'].all()
    trained=solve_two_stage([(lb,price)],activation_profile={'up':.5,'dn':.5})
    out=evaluate(trained['first'],[(lb,price)],activation_profile={'up':1.,'dn':0.})
    assert len(out)==1
    assert not out.feasible.iloc[0] and out.cost.isna().all()


def test_negative_price_cannot_buy_simultaneous_scheduled_cycles():
    lb=load_inputs().iloc[:4]
    r=solve(lb,np.full(4,-1000.),reg=False)
    assert np.minimum(r['trace'].charge,r['trace'].discharge).max()<1e-7
    regulated=solve(lb,np.full(4,-1000.))
    t=regulated['trace']
    assert np.minimum(t.charge+t.discharge,t.reg_up+t.reg_dn).max()<1e-7


def test_invalid_activation_is_rejected():
    with pytest.raises(ValueError,match='activation'):
        solve(load_inputs().iloc[:4],tariff(4),activation_profile={'up':.7,'dn':.7})
