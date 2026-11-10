from dataclasses import replace
import json
import numpy as np
import pandas as pd
import pytest
import linopy
from config import Config,load_inputs,tariff,ROOT
from dispatch import solve,optimize,window_weights
from degradation import add_cost,unit_cost
from intervals import Interval
from independent_verify import verify_trace
from jobs import decompose
from stochastic import solve_two_stage,evaluate,scenarios
from dro import worst_probabilities
from sizing import candidates,select,pareto
from economics import crf,lifetime
from migration import solve_migration
from verify import regress,verify_run

@pytest.fixture(scope='module')
def inputs():
    return load_inputs().iloc[:8].reset_index(drop=True),np.array([300.,300,300,600,1000,1000,600,300])

@pytest.fixture(scope='module')
def solved(inputs): return solve(*inputs)

def test_degradation_anchor_and_sos2():
    m=linopy.Model(); x=m.add_variables(lower=.3,upper=.3,name='throughput')
    m.add_objective(add_cost(m,x,1.,'test'))
    bounds=optimize(m)
    assert float(m.objective.value)/.3==pytest.approx(22.462056,abs=.01)
    assert bounds.lo<=float(m.objective.value)<=bounds.hi
    assert unit_cost(.05)==unit_cost(.3)

def test_exported_constraints_and_tamper(solved):
    assert verify_trace(solved['trace'],solved)['pass'].all()
    corrupt=solved['trace'].copy(); corrupt.loc[2,'soc']+=.1
    assert not verify_trace(corrupt,solved)['pass'].all()
    corrupt.loc[3,'grid']=np.nan
    assert not verify_trace(corrupt,solved)['pass'].all()

def test_fractional_circular_reserve():
    w=window_weights(8,.05,.25)
    assert np.allclose(w.sum(axis=1),.05)
    assert w[-1,0]==.05
    with pytest.raises(ValueError): window_weights(8,3,.25)

def test_interval_endpoints():
    a,b,c,d=Interval(10,12),Interval(4,6),Interval(5,8),Interval(1,2)
    assert a-b==Interval(4,8)
    assert b+c-a-d==Interval(-5,3)
    assert (a-b)+(c-d)==Interval(7,15)
    with pytest.raises(ValueError): Interval(2,1)

def test_one_scenario_anchor(inputs,solved):
    r=solve_two_stage([inputs])
    assert r['cost']==pytest.approx(solved['cost'],abs=1e-6)
    assert r['nonanticipation']==0

def test_stochastic_nonanticipation_and_oos(inputs):
    cases=scenarios(*inputs,n=2)
    r=solve_two_stage(cases)
    assert r['nonanticipation']==0
    out=evaluate(r['first'],cases)
    assert len(out)==2 and np.isfinite(out.cost).all()

def test_dro_probability_and_endogenous_dual(inputs):
    values=[1.,3.,10.]; previous=-np.inf
    for radius in (0,.2,.5,1.,2.):
        r=worst_probabilities(values,radius=radius)
        assert sum(r['probabilities'])==pytest.approx(1)
        assert r['l1']<=radius+1e-8 and r['cost']>=previous-1e-8
        previous=r['cost']
    r=solve_two_stage(scenarios(*inputs,n=2),radius=.4)
    costs=[x['cost'] for x in r['scenarios']]
    assert worst_probabilities(costs,radius=.4)['cost']==pytest.approx(r['cost'],abs=.01)

def test_deadlines_and_l1(inputs,solved):
    for key,w in [('train',2),('infer',0),('batch',8),('deliv',4)]:
        assert decompose(solved['trace']['defer_'+key],solved['trace']['recover_'+key],w)['passed']
    assert not decompose([1,0,0,0],[0,0,0,1],.25)['passed']

def test_sizing_anchor_monotonic_and_frontier(inputs):
    d=candidates(*inputs,counts=[5,6,10])
    for _,row in d.iterrows():
        assert row.cost==pytest.approx(solve(*inputs,modules=int(row.modules))['cost'],abs=1e-3)
    selected=select(d)
    assert selected['master_gap']<1e-4
    larger=candidates(inputs[0]*1.2,inputs[1],counts=[5,6,10])
    # Monotonicity is an observed acceptance property on this load sweep, not a theorem.
    assert select(larger)['modules']>=selected['modules']
    f=pareto(d)
    assert (f.eens.diff().dropna()<=1e-9).all()

def test_finance_and_coupled_lifetime(inputs):
    assert crf(0,10)==.1
    assert crf(.08,15)>.08
    c=replace(Config(),years=3,calendar_fade=.15)
    r=lifetime(*inputs,6,20000.,c)
    assert r['replacements']>=1
    assert len(r['cashflows'])==3

def test_migration_conservation():
    load=np.ones((2,4)); prices=np.array([[1000]*4,[300]*4])
    local=solve_migration(load,prices,3.,enabled=False)
    global_=solve_migration(load,prices,3.)
    assert global_['conservation']<1e-8 and global_['migrated_mwh']>0
    assert global_['cost']<=local['cost']

def test_bad_inputs(inputs):
    with pytest.raises(ValueError): solve(inputs[0],inputs[1][:-1])
    with pytest.raises(ValueError): solve(*inputs,cfg=replace(Config(),eta=2))
    with pytest.raises(ValueError): solve_two_stage([],radius=.2)
    with pytest.raises(ValueError): worst_probabilities([1,2],q=[.3,.3])

def test_regression_missing_column(tmp_path):
    a=tmp_path/'a.csv'; b=tmp_path/'b.csv'
    pd.DataFrame([dict(arch='N',policy='x',cost=1,peak=2)]).to_csv(a,index=False)
    pd.DataFrame([dict(arch='N',policy='x',cost=1)]).to_csv(b,index=False)
    assert regress(a,b)==1

def test_provenance_sensitivity_coverage():
    from provenance import table
    p=table()
    assert p.source_type.notna().all() and p.source.str.len().min()>0
    assert (p.low<=p.high).all()

def test_rolling_states_and_terminal(inputs):
    from mpc import rolling
    r=rolling(*inputs,horizon=4,forecast_error=.1)
    assert r['terminal_error']<1e-3
    assert len(r['trace'])==8
