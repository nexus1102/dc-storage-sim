"""Solver certification intervals, separate from statistical confidence intervals."""
from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class Interval:
    lo: float
    hi: float

    def __post_init__(self):
        if not np.isfinite([self.lo,self.hi]).all() or self.lo > self.hi:
            raise ValueError('invalid interval')

    def __sub__(self, other):
        return Interval(self.lo-other.hi, self.hi-other.lo)

    def __add__(self, other):
        return Interval(self.lo+other.lo, self.hi+other.hi)

def bootstrap(x, seed=20261110, n_boot=2000):
    x = np.asarray(x, float)
    if x.size == 0 or not np.isfinite(x).all():
        raise ValueError('bootstrap requires finite nonempty paired observations')
    rng = np.random.default_rng(seed)
    samples = x[rng.integers(len(x), size=(n_boot,len(x)))].mean(axis=1)
    return float(x.mean()), *map(float,np.quantile(samples,[.025,.975]))
