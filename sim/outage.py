"""Independent fixed-IT/cooling outage replay; no job shedding or Tier claim.
Shared spare selects one path for the event. Generator is full-load after ready.
"""
import numpy as np
from scipy.optimize import milp, Bounds, LinearConstraint


def replay(topology,soc,site,duration_h,cfg,fault,*,transfer_s=0.,generator_ready_s=None):
    topology.validate();soc=np.asarray(soc,float);site=np.asarray(site,float)
    caps=np.array([cfg.soc_max*u.energy*u.soh for u in topology.units])
    if (soc.shape!=caps.shape or not np.isfinite(soc).all() or (soc<0).any() or (soc>caps+1e-7).any()
        or site.ndim!=1 or not len(site) or not np.isfinite(site).all() or (site<0).any()
        or not np.isfinite([duration_h,transfer_s]).all() or duration_h<=0 or transfer_s<0
        or duration_h>len(site)*cfg.dt+1e-10):raise ValueError('invalid outage trajectory')
    if generator_ready_s is not None and (not np.isfinite(generator_ready_s) or generator_ready_s<0):raise ValueError('invalid generator readiness')
    if not set(fault.units)<={u.name for u in topology.units} or not set(fault.paths)<={p.name for p in topology.paths}:raise ValueError('unknown fault')
    ready=duration_h if generator_ready_s is None else min(duration_h,generator_ready_s/3600.)
    transfer=min(duration_h,transfer_s/3600.)
    cuts=sorted(set([0.,duration_h,min(transfer,ready),ready]+[min(k*cfg.dt,duration_h) for k in range(1,len(site)+1)]))
    h=np.diff(cuts);mid=(np.array(cuts[:-1])+np.array(cuts[1:]))/2
    need=site[np.minimum((mid/cfg.dt).astype(int),len(site)-1)]*(mid<ready)
    ups=(mid>=transfer)&(mid<ready)
    edges=[(i,p) for i,u in enumerate(topology.units) if u.name not in fault.units for p in u.paths if p not in fault.paths]
    links=[(p.name,g.name) for p in topology.paths if p.name not in fault.paths for g in topology.groups if p.name in g.paths]
    switches=[edge for edge in edges if len(topology.units[edge[0]].paths)>1]
    nt,ne,nl,nz=len(h),len(edges),len(links),len(switches)
    per=ne+nl+1;nv=nt*per+nz; rows=[];lb=[];ub=[]
    def con(coeff,lower=-np.inf,upper=np.inf):
        row=np.zeros(nv)
        for k,value in coeff.items():row[k]=value
        rows.append(row);lb.append(lower);ub.append(upper)
    bounds=np.full(nv,np.inf)
    for t in range(nt):
        bounds[t*per+ne+nl]=need[t]*ups[t]
        for i,u in enumerate(topology.units):con({t*per+k:1 for k,e in enumerate(edges) if e[0]==i},upper=u.power)
        for k,edge in enumerate(edges):
            if edge in switches:con({t*per+k:1,nt*per+switches.index(edge):-topology.units[edge[0]].power},upper=0)
        for p in topology.paths:
            flow={t*per+k:1 for k,e in enumerate(edges) if e[1]==p.name};con(flow,upper=p.power)
            flow.update({t*per+ne+k:-1 for k,e in enumerate(links) if e[0]==p.name});con(flow,0,0)
        for g in topology.groups:
            flow={t*per+ne+k:1 for k,e in enumerate(links) if e[1]==g.name}
            flow[t*per+ne+nl]=-g.fraction;con(flow,0,0)
    for i,u in enumerate(topology.units):
        con({t*per+k:h[t]/cfg.eta for t in range(nt) for k,e in enumerate(edges) if e[0]==i},upper=soc[i])
        ids=[nt*per+k for k,e in enumerate(switches) if e[0]==i]
        if ids:con({k:1 for k in ids},upper=1)
    objective=np.zeros(nv)
    for t in range(nt):objective[t*per+ne+nl]=-h[t]
    bounds[nt*per:]=1.;integer=np.zeros(nv);integer[nt*per:]=1
    opt=milp(objective,integrality=integer,bounds=Bounds(np.zeros(nv),bounds),constraints=LinearConstraint(np.asarray(rows),lb,ub),options={'mip_rel_gap':1e-9})
    if not opt.success:raise RuntimeError(opt.message)
    delivered=np.array([opt.x[t*per+ne+nl] for t in range(nt)])
    spent=np.array([sum(opt.x[t*per+k]*h[t]/cfg.eta for t in range(nt) for k,e in enumerate(edges) if e[0]==i) for i in range(len(soc))])
    short=np.maximum(0.,need-delivered)
    if (spent>soc+1e-7).any():raise AssertionError('outage SOC residual exceeds tolerance')
    return dict(eens_mwh=float(short@h),service_preserved=bool(short.max()<1e-7),soc_end=np.maximum(0.,soc-spent).tolist(),required_mwh=float(need@h),
                interval_h=h.tolist(),demand_mw=need.tolist(),served_mw=delivered.tolist(),fault=fault.name,
                policy='fixed IT and cooling; no shedding; static spare route',dynamic_certification=False)


def recharge(topology,soc,target,site,cfg):
    """Conservative first-path recharge after full repair, no routine arbitrage."""
    current=np.asarray(soc,float).copy();target=np.asarray(target,float);site=np.asarray(site,float)
    caps=np.array([cfg.soc_max*u.energy*u.soh for u in topology.units])
    if current.shape!=caps.shape or target.shape!=caps.shape or site.ndim!=1 or not np.isfinite(np.r_[current,target,site]).all() or (current<0).any() or (target<0).any() or (current>caps+1e-7).any() or (target>caps+1e-7).any() or (site<0).any() or (site>cfg.grid_max).any():raise ValueError('invalid recharge state')
    rows=[]
    for load in site:
        head=cfg.grid_max-load;path_head={p.name:p.power for p in topology.paths};power=[]
        for i,u in enumerate(topology.units):
            p=u.paths[0];ch=max(0.,min(u.power,cfg.charge_rate*u.energy*u.soh,path_head[p],head,(target[i]-current[i])/(cfg.eta*cfg.dt)))
            current[i]+=ch*cfg.eta*cfg.dt;head-=ch;path_head[p]-=ch;power.append(ch)
        rows.append(power)
    return dict(recovered=bool((current>=target-1e-7).all()),soc_end=current.tolist(),charge_mwh=float(np.sum(rows)*cfg.dt),unit_charge_mw=rows,
                policy='after full repair; fixed first path; no routine arbitrage')
