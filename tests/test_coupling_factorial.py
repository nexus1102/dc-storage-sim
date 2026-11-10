from config import Config,load_inputs,tariff
from physical_topology import architecture
from foundation_experiments import example_contracts
from coupling import factorial
from independent_verify import verify_trace
from research_verify import verify_research


def test_four_cells_keep_devices_contracts_and_cooling():
    load=load_inputs().iloc[:4];cfg=Config();a=architecture('N+1')
    contracts=example_contracts(load,cfg);arms=factorial(load,tariff(4),a,cfg,contracts)
    assert len(arms)==4
    for key,r in arms.items():
        assert r['trace'].ac.sum()>0
        assert r['options']['physical_topology']==a.snapshot()
        assert r['options']['service_contracts']==contracts.snapshot()
        assert verify_trace(r['trace'],r)['pass'].all()
        assert verify_research(r['trace'],r,r['physical_trace'],r['service_trace'])['pass'].all()
        assert r['options']['fixed_operation'] is not None if key.startswith('sequential') else r['options']['fixed_operation'] is None
    for reg in ('reg','no_reg'):
        assert arms['joint_'+reg]['lo']<=arms['sequential_'+reg]['hi']+.01
