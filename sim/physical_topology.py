"""Explicit UPS units, power paths, transfer edges and deterministic contingencies.

Names alone never imply Tier certification. 2N+1 here means two N paths plus
ONE shared, switchable spare; 2(N+1) has one dedicated spare in EACH path.
All transitions are ideal static transfers; interruption dynamics need site data.
"""
from dataclasses import dataclass,asdict
import numpy as np
import pandas as pd
import xarray as xr

@dataclass(frozen=True)
class Unit:
    name: str
    paths: tuple[str,...]
    energy: float=.5
    power: float=2.
    soh: float=1.

@dataclass(frozen=True)
class PowerPath:
    name: str
    power: float=10.

@dataclass(frozen=True)
class LoadGroup:
    name: str
    paths: tuple[str,...]
    fraction: float=1.

@dataclass(frozen=True)
class Fault:
    name: str
    units: tuple[str,...]=()
    paths: tuple[str,...]=()

@dataclass(frozen=True)
class Topology:
    name: str
    units: tuple[Unit,...]
    paths: tuple[PowerPath,...]
    groups: tuple[LoadGroup,...]
    design_load: float=10.
    bidirectional_interface: bool=True

    def validate(self):
        names={u.name for u in self.units}; paths={p.name for p in self.paths}
        if len(names)!=len(self.units) or len(paths)!=len(self.paths): raise ValueError('duplicate physical IDs')
        if not names or not paths or not self.groups: raise ValueError('empty physical topology')
        if self.design_load<=0 or not np.isfinite(self.design_load):raise ValueError('invalid design load')
        if abs(sum(g.fraction for g in self.groups)-1)>1e-9: raise ValueError('load fractions must sum to one')
        for u in self.units:
            if not u.paths or len(set(u.paths))!=len(u.paths) or not set(u.paths)<=paths or u.energy<=0 or u.power<=0 or not 0<u.soh<=1 or not np.isfinite([u.energy,u.power,u.soh]).all():
                raise ValueError('invalid UPS unit')
        for p in self.paths:
            if p.power<=0 or not np.isfinite(p.power): raise ValueError('invalid path rating')
        for g in self.groups:
            if not g.paths or not set(g.paths)<=paths or g.fraction<=0 or not np.isfinite(g.fraction): raise ValueError('invalid load connection')
        return self

    @property
    def energy(self): return sum(u.energy*u.soh for u in self.units)

    def faults(self,scope='module'):
        if scope not in ('normal','module','path','all'): raise ValueError('invalid contingency scope')
        states=[Fault('grid_outage')]
        if scope in ('module','all'): states += [Fault('lose_'+u.name,units=(u.name,)) for u in self.units]
        if scope in ('path','all'): states += [Fault('lose_path_'+p.name,paths=(p.name,)) for p in self.paths]
        return states

    def snapshot(self): return asdict(self)

def architecture(name,n=5,energy=.5,power=2.,dual_cord=True):
    if n<1 or n!=int(n): raise ValueError('N must be positive integer')
    if name not in ('N','N+1','2N','2N+1','2(N+1)'): raise ValueError(name)
    dual=name.startswith('2')
    paths=('A','B') if dual else ('A',)
    units=[Unit(f'{p}{i}',(p,),energy,power) for p in paths for i in range(n)]
    if name=='N+1': units.append(Unit('A_spare',('A',),energy,power))
    if name=='2N+1': units.append(Unit('shared_spare',paths,energy,power))
    if name=='2(N+1)': units += [Unit(p+'_spare',(p,),energy,power) for p in paths]
    groups=(LoadGroup('critical',paths),) if dual_cord or not dual else (
        LoadGroup('rack_A',('A',),.5),LoadGroup('rack_B',('B',),.5))
    return Topology(name,tuple(units),tuple(PowerPath(p,n*power) for p in paths),groups,n*power).validate()

def add_security(m,site,topology,cfg,prefix,scope='module'):
    """Component SOC and outage routing, attached to the shared operational kernel.

Outage demand uses the maximum forward site load over the required ride window.
This is a sufficient inner approximation for time-varying demand, exact for a
constant demand bound; no expected-duration CVaR approximation is used here.
"""
    topology.validate()
    if not topology.bidirectional_interface: raise ValueError('grid services require a verified bidirectional interface')
    n=site.sizes['t']; ix=pd.Index(range(n),name='t')
    def vector(name,upper=np.inf,binary=False):
        return m.add_variables(coords=[ix],name=prefix+'_'+name,**({'binary':True} if binary else {'lower':0,'upper':upper}))
    def con(name,x): m.add_constraints(x,name=prefix+'_'+name)
    unit_soc={u.name:vector('soc_'+u.name,cfg.soc_max*u.energy*u.soh) for u in topology.units}
    charge={u.name:vector('charge_'+u.name,min(u.power,cfg.charge_rate*u.energy*u.soh)) for u in topology.units}
    discharge={u.name:vector('discharge_'+u.name,u.power) for u in topology.units}
    reg_up={u.name:vector('reg_up_'+u.name,u.power) for u in topology.units}
    reg_dn={u.name:vector('reg_dn_'+u.name,min(u.power,cfg.charge_rate*u.energy*u.soh)) for u in topology.units}
    for u in topology.units:
        con('soc_recur_'+u.name,unit_soc[u.name]==unit_soc[u.name].roll(t=1)+cfg.dt*(cfg.eta*charge[u.name]-discharge[u.name]/cfg.eta))
        con('unit_up_'+u.name,discharge[u.name]+reg_up[u.name]<=u.power)
        con('unit_down_'+u.name,charge[u.name]+reg_dn[u.name]<=min(u.power,cfg.charge_rate*u.energy*u.soh))
        con('unit_reg_energy_'+u.name,unit_soc[u.name]+cfg.eta*cfg.reg_duration*reg_dn[u.name]<=cfg.soc_max*u.energy*u.soh)
    peak=vector('outage_peak')
    slots=max(1,int(np.ceil(cfg.ride_h/cfg.dt)))
    for k in range(slots): con('outage_peak_'+str(k),peak>=site.roll(t=-(k+1)))
    # Split charge/discharge at the AC connections, enforcing actual path ratings.
    normal={}; normal_charge={}; normal_up={}; normal_dn={}
    for u in topology.units:
        switches=[]
        for p in u.paths:
            a=vector('normal_'+u.name+'_'+p,u.power); c=vector('normal_charge_'+u.name+'_'+p,u.power)
            ur=vector('normal_up_'+u.name+'_'+p,u.power);dr=vector('normal_dn_'+u.name+'_'+p,u.power)
            normal[u.name,p]=a; normal_charge[u.name,p]=c;normal_up[u.name,p]=ur;normal_dn[u.name,p]=dr
            if len(u.paths)>1:
                z=vector('normal_switch_'+u.name+'_'+p,binary=True);switches.append(z)
                con('normal_route_'+u.name+'_'+p,a+c+ur+dr<=u.power*z)
        con('normal_unit_'+u.name,sum(normal[u.name,p] for p in u.paths)==discharge[u.name])
        con('normal_charge_unit_'+u.name,sum(normal_charge[u.name,p] for p in u.paths)==charge[u.name])
        con('normal_up_unit_'+u.name,sum(normal_up[u.name,p] for p in u.paths)==reg_up[u.name])
        con('normal_dn_unit_'+u.name,sum(normal_dn[u.name,p] for p in u.paths)==reg_dn[u.name])
        if switches: con('normal_one_path_'+u.name,sum(switches)<=1)
    for p in topology.paths:
        con('normal_path_'+p.name,sum(normal[u.name,p.name]+normal_charge[u.name,p.name]+normal_up[u.name,p.name]+normal_dn[u.name,p.name] for u in topology.units if p.name in u.paths)<=p.power)
    routes={}; loads={}; states=topology.faults(scope)
    for state in states:
        for u in topology.units:
            active=[p for p in u.paths if p not in state.paths and u.name not in state.units]
            switches=[]; incoming=[]
            for p in active:
                f=vector('fault_'+state.name+'_'+u.name+'_'+p,u.power*cfg.ride_h)
                routes[state.name,u.name,p]=f; incoming.append(f)
                if len(u.paths)>1:
                    z=vector('switch_'+state.name+'_'+u.name+'_'+p,binary=True);switches.append(z)
                    con('switch_flow_'+state.name+'_'+u.name+'_'+p,f<=u.power*cfg.ride_h*z)
            if incoming:
                con('fault_energy_'+state.name+'_'+u.name,sum(incoming)<=cfg.eta*unit_soc[u.name]-cfg.reg_duration*reg_up[u.name])
                con('fault_power_'+state.name+'_'+u.name,sum(incoming)<=u.power*cfg.ride_h)
            if switches: con('one_path_'+state.name+'_'+u.name,sum(switches)<=1)
        for p in topology.paths:
            supplied=[v for (s,_,path),v in routes.items() if s==state.name and path==p.name]
            outgoing=[]
            for g in topology.groups:
                if p.name in g.paths and p.name not in state.paths:
                    f=vector('load_'+state.name+'_'+p.name+'_'+g.name)
                    loads[state.name,p.name,g.name]=f;outgoing.append(f)
            # Empty paths remain represented (0*peak avoids constant constraints).
            supply=sum(supplied,0*peak); demand=sum(outgoing,0*peak)
            con('fault_path_balance_'+state.name+'_'+p.name,supply==demand)
            con('fault_path_power_'+state.name+'_'+p.name,supply<=p.power*cfg.ride_h)
        for g in topology.groups:
            received=sum((v for (s,_,group),v in loads.items() if s==state.name and group==g.name),0*peak)
            con('fault_load_'+state.name+'_'+g.name,received>=peak*g.fraction*cfg.ride_h)
    return dict(soc=unit_soc,charge=charge,discharge=discharge,reg_up=reg_up,reg_dn=reg_dn,normal=normal,normal_charge=normal_charge,normal_up=normal_up,normal_dn=normal_dn,
                routes=routes,loads=loads,peak=peak,states=states,topology=topology)

def export_security(model):
    rows=[]
    for kind in ('soc','charge','discharge','reg_up','reg_dn','normal','normal_charge','normal_up','normal_dn','routes','loads'):
        for key,var in model[kind].items():
            key=(key,) if isinstance(key,str) else key
            for t,value in enumerate(np.asarray(var.solution)):
                rows.append(dict(kind=kind,key='|'.join(key),t=t,value=float(value)))
    for t,value in enumerate(np.asarray(model['peak'].solution)):
        rows.append(dict(kind='peak',key='site',t=t,value=float(value)))
    return pd.DataFrame(rows)
