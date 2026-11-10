"""Independent exported-result verifier for component routing and useful-work SLAs.
No model objects, linopy variables or constraint builders are imported.
"""
import numpy as np
import pandas as pd


def verify_capability(reference,result,start,duration_slots):
    """Independent event/state attribution audit using exported witnesses only."""
    witness=result['witness'];a=reference['trace'];b=witness['trace'];rows=[]
    def check(name,res):rows.append(dict(check=name,residual=float(res),tol=1e-6,**{'pass':bool(np.isfinite(res) and res<=1e-6)}))
    p=result['power_mw'];dt=witness['config']['dt'];event=slice(start,start+duration_slots)
    check('firm_grid_delivery',float((b.grid.iloc[event]+p-a.grid.iloc[event]).max()))
    check('delivered_energy_unit',abs(result['delivered_mwh']-p*duration_slots*dt))
    check('power_nonnegative',-p)
    for key in ('grid','it','ac','cold'):
        if start:check('executed_'+key,np.abs(a[key].iloc[:start]-b[key].iloc[:start]).max())
    if result['mode']=='battery_only':
        for key in ('it','ac'):check('attribution_'+key,np.abs(a[key]-b[key]).max())
    for kind in ('soc','charge','discharge'):
        aa=reference['physical_trace'];bb=witness['physical_trace']
        # Initial cyclic state is at n-1 when start=0; otherwise the history
        # includes the end-state just before the event.
        mask=lambda x:(x.kind==kind)&((x.t<start)|((x.t==len(a)-1)&(kind=='soc')&(start==0)))
        av=aa[mask(aa)].sort_values(['key','t']).value.to_numpy();bv=bb[mask(bb)].sort_values(['key','t']).value.to_numpy()
        if len(av):check('physical_history_'+kind,np.abs(av-bv).max())
    if 'service_trace' in reference:
        aa=reference['service_trace'];bb=witness['service_trace']
        for name in aa.job.unique():
            ar=aa[aa.job==name].sort_values('t');br=bb[bb.job==name].sort_values('t')
            check('no_extra_unmet_'+name,float(br.unmet.max()-ar.unmet.max()))
            if start:
                for key in ('work_mw','on','start'):check('service_history_'+name+'_'+key,np.abs(ar[key].iloc[:start].to_numpy()-br[key].iloc[:start].to_numpy()).max())
    return pd.DataFrame(rows)

def verify_research(trace,meta,physical=None,services=None):
    rows=[];c=meta['config'];options=meta['options'];dt=c['dt'];n=len(trace)
    def add(name,res,tol=1e-6):
        res=float(res);rows.append(dict(check=name,residual=res if np.isfinite(res) else np.inf,tol=tol,
                                       **{'pass':bool(np.isfinite(res) and res<=tol)}))
    def eq(name,x):add(name,float(np.abs(np.asarray(x)).max()))
    if physical is not None:
        spec=options['physical_topology']; units={u['name']:u for u in spec['units']};paths={p['name']:p for p in spec['paths']}
        if not np.isfinite(physical.value).all(): add('physical_finite',np.inf);return pd.DataFrame(rows)
        def v(kind,key):
            d=physical[(physical.kind==kind)&(physical.key==key)].sort_values('t')
            if len(d)!=n or list(d.t)!=list(range(n)):raise ValueError('missing or duplicate physical time rows')
            return d.value.to_numpy()
        add('physical_nonnegative',max(0.,-physical.value.min()))
        for kind,column in (('soc','soc'),('charge','charge'),('discharge','discharge'),('reg_up','reg_up'),('reg_dn','reg_dn')):
            eq('unit_aggregate_'+kind,sum(v(kind,u) for u in units)-trace[column])
        for name,u in units.items():
            soc=v('soc',name);ch=v('charge',name);dis=v('discharge',name);ru=v('reg_up',name);rd=v('reg_dn',name)
            delta=dt*(c['eta']*ch-dis/c['eta'])
            if c.get('accounting_version',3)==4:
                activation=options['activation_profile']
                delta+=dt*(c['eta']*np.asarray(activation['dn'])*rd-np.asarray(activation['up'])*ru/c['eta'])
            eq('unit_soc_'+name,soc-np.roll(soc,1)-delta)
            add('unit_limits_'+name,max((soc-c['soc_max']*u['energy']*u['soh']).max(),(dis+ru-u['power']).max(),
                    (ch+rd-min(u['power'],c['charge_rate']*u['energy']*u['soh'])).max(),
                    (soc+c['eta']*c['reg_duration']*rd-c['soc_max']*u['energy']*u['soh']).max()))
            for kind in ('normal','normal_charge','normal_up','normal_dn'):
                target={'normal':'discharge','normal_charge':'charge','normal_up':'reg_up','normal_dn':'reg_dn'}[kind]
                eq(kind+'_'+name,sum(v(kind,name+'|'+p) for p in u['paths'])-v(target,name))
            occupied=sum((sum(v(k,name+'|'+p) for k in ('normal','normal_charge','normal_up','normal_dn'))>1e-7).astype(int) for p in u['paths'])
            if len(u['paths'])>1:add('normal_switch_exclusive_'+name,occupied.max()-1)
        for name,p in paths.items():
            total=sum(v(k,u+'|'+name) for u,x in units.items() if name in x['paths'] for k in ('normal','normal_charge','normal_up','normal_dn'))
            add('normal_path_rating_'+name,(total-p['power']).max())
        peak=v('peak','site');steps=max(1,int(np.ceil(c['ride_h']/dt)))
        for k in range(steps):add('forward_outage_bound_'+str(k),(np.roll(trace.site.to_numpy(),-(k+1))-peak).max())
        states=[('grid_outage',[],[])]
        if options['contingency_scope'] in ('module','all'):states += [('lose_'+u,[u],[]) for u in units]
        if options['contingency_scope'] in ('path','all'):states += [('lose_path_'+p,[],[p]) for p in paths]
        routes=physical[physical.kind=='routes']; loads=physical[physical.kind=='loads']
        for state,failed_units,failed_paths in states:
            for name,u in units.items():
                supplied=[];occupied=[]
                for p in u['paths']:
                    key=state+'|'+name+'|'+p
                    d=routes[routes.key==key]
                    if name in failed_units or p in failed_paths:
                        add('failed_isolation_'+key,0 if d.empty else abs(d.value).max());continue
                    f=v('routes',key);supplied.append(f);occupied.append((f>1e-7).astype(int))
                if supplied:
                    total=sum(supplied)
                    add('contingency_unit_'+state+'_'+name,max((total-c['eta']*v('soc',name)+c['reg_duration']*v('reg_up',name)).max(),(total-u['power']*c['ride_h']).max()))
                if len(u['paths'])>1 and occupied:add('spare_exclusive_'+state+'_'+name,sum(occupied).max()-1)
            for name,p in paths.items():
                inflow=sum((v('routes',state+'|'+u+'|'+name) for u,x in units.items() if u not in failed_units and name in x['paths'] and name not in failed_paths),np.zeros(n))
                outflow=sum((v('loads',state+'|'+name+'|'+g['name']) for g in spec['groups'] if name in g['paths'] and name not in failed_paths),np.zeros(n))
                eq('fault_path_balance_'+state+'_'+name,inflow-outflow)
                add('fault_path_capacity_'+state+'_'+name,(inflow-p['power']*c['ride_h']).max())
            for g in spec['groups']:
                actual=sum((v('loads',state+'|'+p+'|'+g['name']) for p in g['paths'] if p not in failed_paths),np.zeros(n))
                add('fault_service_'+state+'_'+g['name'],(peak*g['fraction']*c['ride_h']-actual).max())
    contract=options.get('service_contracts')
    if contract and services is not None:
        totals={k:np.zeros(n) for k in ('train','batch','deliv')};service_cost=0.
        add('service_finite',0 if np.isfinite(services.drop(columns=['job','kind']).to_numpy()).all() else np.inf)
        for j in contract['jobs']:
            df=services[services.job==j['name']].sort_values('t')
            if len(df)!=n or list(df.t)!=list(range(n)):raise ValueError('missing job time rows')
            x=df.work_mw.to_numpy();on=df.on.to_numpy();start=df.start.to_numpy();unmet=float(df.unmet.iloc[0])
            eq('useful_work_'+j['name'],dt*x.sum()+unmet-j['work'])
            add('job_resource_'+j['name'],max(-x.min(),(x-j['max_power']).max(),-unmet,unmet-(j['work'] if j['soft_deadline'] else 0)))
            outside=(np.arange(n)<j['release'])|(np.arange(n)>=j['deadline'])
            if outside.any():eq('job_window_'+j['name'],x[outside])
            if j['kind']=='train':
                eq('restart_transition_'+j['name'],start-np.maximum(0,on-np.r_[0.,on[:-1]]))
                eq('binary_on_'+j['name'],on-np.rint(on))
                add('training_on_bounds_'+j['name'],max(-on.min(),on.max()-1))
                if outside.any():eq('training_on_window_'+j['name'],on[outside])
                add('training_power_'+j['name'],max((x-j['max_power']*on).max(),(j['min_power']*on-x).max()))
                for t in np.flatnonzero(start>.5):
                    add(f'checkpoint_{j["name"]}_{t}',j['min_run']-on[t:min(n,t+j['min_run'])].sum())
                    add(f'training_deadline_{j["name"]}_{t}',t+j['min_run']-j['deadline'])
            totals[j['kind']]+=x+j['restart_energy']/dt*start
            service_cost+=j['unmet_price']*unmet
        for kind,x in totals.items():
            if 'service_'+kind in trace:eq('service_meter_'+kind,trace['service_'+kind]-x)
        s=contract.get('inference')
        if s:
            mu=services[services.kind=='infer'].sort_values('t').mu.to_numpy();arrival=np.array(s['arrivals'])
            add('inference_response_tail',(np.exp(-np.maximum(0.,mu-arrival)*s['response_s'])-s['violation_probability']).max())
            eq('inference_meter',trace.service_infer-s['idle_mw']-mu*s['joules_per_request']/1e6)
            add('inference_capacity',trace.service_infer.max()-s['max_power'])
        eq('service_penalty',service_cost-meta.get('service_cost',0.))
    return pd.DataFrame(rows)
