import numpy as np
import pandas as pd
import pytest
from config import Config,load_inputs,tariff
from dispatch import solve
from independent_verify import verify_trace
from sizing import legacy_anchors,event_eens
from stochastic import solve_two_stage,evaluate
from mpc import rolling

def test_five_historical_v2_anchors():
    d=legacy_anchors(load_inputs())
    assert d.passed.all() and d.error.max()<1e-3

def test_reliability_constrained_redispatch():
    load=load_inputs().iloc[:8]; pr=tariff(8)
    r=solve(load,pr,6,eens_limit=0.)
    assert event_eens(r)<1e-6
    assert verify_trace(r['trace'],r)['pass'].all()

def test_oos_fixed_commitments_can_record_shortfall():
    load=load_inputs().iloc[:8]; pr=tariff(8)
    trained=solve_two_stage([(load,pr)])
    evaluated=evaluate(trained['first'],[(load*1.3,pr)])
    assert len(evaluated)==1 and np.isfinite(evaluated.cost).all()
    assert evaluated.violation.iloc[0]>0

def test_hierarchical_mpc_terminal():
    load=load_inputs().iloc[:8]; pr=tariff(8)
    r=rolling(load,pr,horizon=4,hierarchical=True)
    assert r['terminal_error']<1e-3

def test_simultaneous_reliability_limits_not_double_counted():
    from reliability import monte_carlo
    r=monte_carlo(soc=np.full(4,.1),load=np.full(4,100.),p_ups=.5,
                  t_gen_med_s=3600.,t_gen_sigma=0.,p_gen_fail=0.,load_sigma=0.,n_scen=100)
    assert r['EENS_MWh']==pytest.approx(99.905)
