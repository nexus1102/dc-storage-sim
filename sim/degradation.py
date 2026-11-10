"""Adjacent-segment SOS2 formulation via binaries (HiGHS has no native SOS2).

Daily equivalent depth is a throughput proxy, not a rainflow cycle depth.
The cost curve interpolates total CNY/MWh-installed, not a bilinear unit rate.
Below .3 and above .9 the nearest calibrated unit rate is held constant.
"""
import numpy as np
import pandas as pd
import xarray as xr
from config import INPUTS

def calibration(path=None):
    d = pd.read_csv(path or INPUTS / 'degradation_calibration.csv')
    return d.DoD.to_numpy(), d.cost_yuan_per_MWh.to_numpy()

def unit_cost(depth, path=None):
    x,y = calibration(path)
    return float(np.interp(depth,x,y))

def add_cost(m, throughput, energy, prefix, mode='sos2', path=None, accounting_version=3):
    # Input is HALF throughput in both versions: retain depth = H/E.
    # Calibration rate is per FULL throughput, so v4 costs are 2*r*H.
    factor=2. if accounting_version==4 else 1.
    if mode == 'legacy':
        return factor * 15. * throughput
    if mode == 'shallow':
        return factor * unit_cost(.3,path) * throughput
    if mode != 'sos2':
        raise ValueError(mode)
    depths,rates = calibration(path)
    x = np.r_[0.,depths,12.]
    y = factor * x * np.r_[rates[0],rates,rates[-1]]
    dim = prefix + '_knot'
    ix = pd.Index(range(len(x)),name=dim)
    lam = m.add_variables(lower=0,coords=[ix],name=prefix+'_lambda')
    z = m.add_variables(binary=True,coords=[pd.Index(range(len(x)-1),name=prefix+'_seg')],name=prefix+'_adj')
    m.add_constraints(lam.sum()==1,name=prefix+'_simplex')
    m.add_constraints(z.sum()==1,name=prefix+'_one_segment')
    for k in range(len(x)):
        support = z[0] if k==0 else z[-1] if k==len(x)-1 else z[k-1]+z[k]
        m.add_constraints(lam[k]<=support,name=f'{prefix}_adjacency_{k}')
    m.add_constraints(throughput==energy*(lam*xr.DataArray(x,coords=[ix])).sum(),name=prefix+'_depth')
    return energy*(lam*xr.DataArray(y,coords=[ix])).sum()
