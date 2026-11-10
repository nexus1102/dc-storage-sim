"""Versioned, serializable research assumptions. Money is CNY, power MW, energy MWh."""
from dataclasses import dataclass, asdict
from pathlib import Path
import os
import numpy as np
import pandas as pd

ROOT = Path(os.environ.get('DC_STORAGE_ROOT',Path(__file__).resolve().parents[1])).resolve()
LAYOUT_VERSION = 2
INPUTS = ROOT / 'data' / 'inputs'
RAW_DATA = ROOT / 'data' / 'raw'
BASELINE = ROOT / 'baseline'
RESULTS = ROOT / 'results'
SUMMARY = RESULTS / 'summary'
KEYS = ('train', 'infer', 'batch', 'deliv')

@dataclass(frozen=True)
class Config:
    accounting_version: int = 4
    dt: float = .25
    eta: float = .95
    soc_max: float = .95
    charge_rate: float = .5
    grid_max: float = 20.
    ride_h: float = .25
    reserve_h: float = .05
    demand_rate: float = 51.2 * 1000 / 30
    reg_duration: float = .5
    reg_capacity_price: float = 0.
    reg_mileage_price: float = 6.
    mileage_ratio: float = 8.
    performance: float = 1.
    activation_beta: float = .05
    thermal_ua: float = .25
    thermal_c: float = 1.
    thermal_cop: float = 3.5
    thermal_range: float = 4.
    thermal_tmax: float = 27.
    thermal_ambient: float = 30.
    ac_max: float = 3.
    flex_scale: float = 1.
    window_scale: float = 1.
    recovery_scale: float = 1.
    capex_kwh: float = 800.
    discount: float = .08
    years: int = 15
    calendar_fade: float = .015
    cycle_fade: float = .00003
    replacement_soh: float = .8
    replacement_fraction: float = .6
    penalty: float = 100000.
    seed: int = 20261110

    def validate(self):
        if self.accounting_version not in (3,4): raise ValueError('unknown accounting version')
        if not 0<=self.activation_beta<=.5: raise ValueError('activation fractions must sum to at most one')
        vals = asdict(self)
        if not all(np.isfinite(v) for v in vals.values()):
            raise ValueError('configuration contains non-finite values')
        positive = ('dt','eta','soc_max','grid_max','reg_duration','thermal_ua',
                    'thermal_c','thermal_cop','thermal_range','ac_max','years','penalty')
        if any(vals[k] <= 0 for k in positive):
            raise ValueError('physical scales and horizon must be positive')
        if not 0 < self.eta <= 1 or not 0 < self.soc_max <= 1:
            raise ValueError('efficiency / SOC outside (0,1]')
        if min(self.ride_h,self.reserve_h,self.flex_scale,self.window_scale,
               self.recovery_scale,self.discount,self.capex_kwh) < 0:
            raise ValueError('negative configuration value')
        if not 0 < self.replacement_soh < 1 or self.years != int(self.years):
            raise ValueError('invalid lifetime configuration')
        return self

    def snapshot(self):
        return asdict(self)

def load_inputs(path=None):
    df = pd.read_csv(path or INPUTS / 'load_business_24h.csv')
    load = df[[f'p_{k}_MW' for k in KEYS]].copy()
    load.columns = KEYS
    return load

def tariff(n=96, dt=.25):
    """June 2025 Jiangsu, 1–10(20) kV two-part agency tariff; not a 2026 quote."""
    hour = np.arange(n) * dt % 24
    return np.where((hour < 6) | ((hour >= 11) & (hour < 13)), 372.,
                    np.where((hour >= 14) & (hour < 22), 991.9, 649.9))
