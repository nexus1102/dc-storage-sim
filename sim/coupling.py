"""Fixed-assets coupling x regulation factorial, preserving cooling and SLAs."""
from dispatch import solve
from capability import standby_reference


def factorial(load,price,topology,cfg,contracts,reference=None):
    reference=reference or standby_reference(load,price,topology,cfg,contracts=contracts)
    fixed={k:reference['trace'][k].to_numpy() for k in ('it','ac')}
    results={}
    for coupled in (False,True):
        for reg in (False,True):
            key=f'{"joint" if coupled else "sequential"}_{"reg" if reg else "no_reg"}'
            results[key]=solve(load,price,len(topology.units),cfg,physical_topology=topology,
                service_contracts=contracts,flex=False,reg=reg,
                fixed_operation=None if coupled else fixed)
    return results
