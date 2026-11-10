"""Explicit useful-work contracts, training restart costs and an M/M/1 SLO proxy.

Useful work uses equivalent full-load MWh, separate from metered electric energy.
No inference delay is inferred from the Alibaba resource-only trace.
"""
from dataclasses import dataclass,asdict
import numpy as np
import pandas as pd
import xarray as xr

@dataclass(frozen=True)
class Job:
    name: str
    kind: str
    release: int
    deadline: int  # exclusive end-slot index
    work: float
    max_power: float
    min_power: float=0.
    min_run: int=1
    restart_energy: float=0.
    soft_deadline: bool=False
    unmet_price: float=100000.

@dataclass(frozen=True)
class InferenceSLO:
    arrivals: tuple[float,...]  # requests/s per steady slot
    response_s: float=.2
    violation_probability: float=.01
    joules_per_request: float=1000.
    idle_mw: float=.02
    max_power: float=2.

@dataclass(frozen=True)
class ServiceContracts:
    jobs: tuple[Job,...]
    inference: InferenceSLO | None=None
    provenance: str='explicit synthetic service contracts; not observed pod deadlines'

    def snapshot(self): return asdict(self)

    def validate(self,n,dt):
        if len({j.name for j in self.jobs})!=len(self.jobs): raise ValueError('duplicate job IDs')
        for j in self.jobs:
            if j.kind not in ('train','batch','deliv') or not 0<=j.release<j.deadline<=n:
                raise ValueError('invalid job type or release/deadline')
            if min(j.work,j.max_power,j.restart_energy,j.min_power)<0 or j.max_power<=0 or j.min_power>j.max_power:
                raise ValueError('invalid job resource scale')
            if j.min_run<1 or j.min_run!=int(j.min_run) or j.unmet_price<=0: raise ValueError('invalid checkpoint or penalty')
            if not np.isfinite([j.work,j.max_power,j.restart_energy,j.min_power,j.unmet_price]).all(): raise ValueError('nonfinite job parameter')
        s=self.inference
        if s:
            if len(s.arrivals)!=n or min(s.arrivals)<0 or not np.isfinite(s.arrivals).all(): raise ValueError('invalid inference arrivals')
            if not 0<s.violation_probability<1 or min(s.response_s,s.joules_per_request,s.max_power)<=0 or s.idle_mw<0:
                raise ValueError('invalid inference SLO')
        return self

def add_services(m,contracts,n,dt,prefix):
    contracts.validate(n,dt)
    ix=pd.Index(range(n),name='t'); jobs={}; cost=0.
    def da(x): return xr.DataArray(np.asarray(x),coords=[ix])
    def con(name,expr):m.add_constraints(expr,name=prefix+'_'+name)
    powers={}
    for j in contracts.jobs:
        active=(np.arange(n)>=j.release)&(np.arange(n)<j.deadline)
        x=m.add_variables(lower=0,upper=da(j.max_power*active),coords=[ix],name=prefix+'_work_'+j.name)
        unmet=m.add_variables(lower=0,upper=j.work if j.soft_deadline else 0.,name=prefix+'_unmet_'+j.name)
        start=on=None; electrical=x.to_linexpr()
        if j.kind=='train':
            on=m.add_variables(binary=True,coords=[ix],name=prefix+'_on_'+j.name)
            start=m.add_variables(binary=True,coords=[ix],name=prefix+'_restart_'+j.name)
            con('on_window_'+j.name,on<=da(active.astype(int)))
            con('power_hi_'+j.name,x<=j.max_power*on)
            con('power_lo_'+j.name,x>=j.min_power*on)
            con('start_init_'+j.name,start[0]==on[0])
            for t in range(1,n):
                con(f'start_lo_{j.name}_{t}',start[t]>=on[t]-on[t-1])
                con(f'start_on_{j.name}_{t}',start[t]<=on[t])
                con(f'start_off_{j.name}_{t}',start[t]+on[t-1]<=1)
            for t in range(n):
                if t+j.min_run>j.deadline: con(f'late_start_{j.name}_{t}',start[t]==0)
                else: con(f'checkpoint_{j.name}_{t}',on[t:t+j.min_run].sum()>=j.min_run*start[t])
            electrical=x+j.restart_energy/dt*start
        con('useful_work_'+j.name,dt*x.sum()+unmet==j.work)
        cost=cost+j.unmet_price*unmet
        powers[j.kind]=powers.get(j.kind,0*x)+electrical
        jobs[j.name]=dict(job=j,work=x,on=on,start=start,unmet=unmet)
    inference=None
    if contracts.inference:
        s=contracts.inference
        mu=m.add_variables(lower=0,coords=[ix],name=prefix+'_inference_mu')
        power=m.add_variables(lower=0,upper=s.max_power,coords=[ix],name=prefix+'_inference_power')
        margin=float(np.log(1/s.violation_probability)/s.response_s)
        con('inference_tail_slo',mu>=da(s.arrivals)+margin)
        con('inference_power',power==s.idle_mw+(s.joules_per_request/1e6)*mu)
        powers['infer']=power; inference=dict(mu=mu,power=power)
    return dict(powers=powers,jobs=jobs,inference=inference,cost=cost,contracts=contracts)

def export_services(model):
    rows=[]
    for name,entry in model['jobs'].items():
        j=entry['job']
        for t,x in enumerate(np.asarray(entry['work'].solution)):
            on=float(entry['on'].solution.values[t]) if entry['on'] is not None else 0.
            start=float(entry['start'].solution.values[t]) if entry['start'] is not None else 0.
            rows.append(dict(job=name,kind=j.kind,t=t,work_mw=float(x),on=on,start=start,unmet=float(entry['unmet'].solution)))
    if model['inference']:
        for t,mu in enumerate(np.asarray(model['inference']['mu'].solution)):
            rows.append(dict(job='inference',kind='infer',t=t,work_mw=0.,on=0.,start=0.,unmet=0.,mu=float(mu)))
    return pd.DataFrame(rows).fillna(0.)
