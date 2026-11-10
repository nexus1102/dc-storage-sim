"""Reconstruct constraints from exported CSV and JSON only; no optimization imports."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd

KEYS=('train','infer','batch','deliv')
SPECS={'train':(.2,2.,.3),'infer':(0.,0.,0.),'batch':(.8,8.,.5),'deliv':(.35,4.,.4)}

def verify_trace(df,meta):
    c=meta['config']; opt=meta['options']; dt=c['dt']; eta=c['eta']; n=len(df)
    e=meta['energy']; p=meta['power']; rows=[]
    def add(name,res,tol=1e-6):
        res=float(res)
        rows.append(dict(check=name,residual=max(0.,res) if np.isfinite(res) else float('inf'),tol=tol,
                         **{'pass':bool(np.isfinite(res) and res<=tol)}))
    def eq(name,a): add(name,np.max(np.abs(np.asarray(a))))
    add('finite',0 if np.isfinite(df.to_numpy()).all() else np.inf)
    if not rows[-1]['pass']: return pd.DataFrame(rows)
    init=opt.get('initial'); term=opt.get('terminal')
    corrected=c.get('accounting_version',3)==4
    activation=opt.get('activation_profile') or {'up':c['activation_beta'],'dn':c['activation_beta']}
    ec=dt*np.asarray(activation['dn'])*df.reg_dn if corrected else 0.
    ed=dt*np.asarray(activation['up'])*df.reg_up if corrected else 0.
    prev=np.r_[df.soc.iloc[-1] if init is None else init['soc'],df.soc.to_numpy()[:-1]]
    eq('soc_recursion',df.soc-prev-dt*(eta*df.charge-df.discharge/eta)-eta*ec+ed/eta)
    add('soc_bounds',max(-df.soc.min(),(df.soc-c['soc_max']*e).max()))
    add('power_bounds',max((df.discharge-p).max(),(df.charge-min(p,c['charge_rate']*e)).max()))
    add('nonnegative',-df.drop(columns=['price']).min().min())
    add('grid_capacity',(df.grid-c['grid_max']).max())
    eq('bus_balance',df.grid+df.discharge-df.charge-df.site-(ec-ed)/dt)
    if corrected:
        eq('agc_meter_charge',df.agc_charge_mwh-ec)
        eq('agc_meter_discharge',df.agc_discharge_mwh-ed)
        eq('scheduled_grid',df.grid_schedule-df.site-df.charge+df.discharge)
        add('schedule_exclusive',np.minimum(df.charge,df.discharge).max())
        add('regulation_schedule_exclusive',np.minimum(df.charge+df.discharge,df.reg_up+df.reg_dn).max())
        add('no_reg_export',(df.reg_up-df.grid_schedule).max())
        add('reg_import_limit',(df.grid_schedule+df.reg_dn-c['grid_max']).max())
    for key,values in (opt.get('fixed_operation') or {}).items():eq('fixed_operation_'+key,df[key]-np.asarray(values))
    it=sum(df['service_'+key] if 'service_'+key in df else df['load_'+key]-df['defer_'+key]+df['recover_'+key] for key in KEYS)
    eq('it_balance',df.it-it)
    eq('site_balance',df.site-df.it-df.ac)
    for key in KEYS:
        f,w,r=SPECS[key]
        override=(opt.get('business_override') or {}).get(key,{})
        f=override.get('f',f); w=override.get('window_h',w); r=override.get('ramp_frac',r)
        f=min(1.,f*c['flex_scale']) if opt['flex'] and 'service_'+key not in df else 0.
        q0=0. if init is None else init.get('queue_'+key,0.)
        q=q0+dt*(df['defer_'+key]-df['recover_'+key]).cumsum()
        eq('queue_balance_'+key,df['queue_'+key]-q)
        add('defer_cap_'+key,(df['defer_'+key]-f*df['load_'+key]).max())
        reference_mw=(init or {}).get('reference_mw',{}).get(key,df['load_'+key].mean())
        add('recover_cap_'+key,(df['recover_'+key]-(r*c['recovery_scale']*reference_mw if f else 0.)).max())
        add('queue_cap_'+key,(q-f*reference_mw*w*c['window_scale']).max())
        steps=int(np.floor(w*c['window_scale']/dt+1e-9))
        if steps<n:
            due=dt*df['defer_'+key].cumsum().to_numpy()[:n-steps]+q0
            done=dt*df['recover_'+key].cumsum().to_numpy()[steps:]
            add('deadline_'+key,np.max(due-done))
        if init is None or term is not None:
            eq('queue_terminal_'+key,q.iloc[-1]-(0. if term is None else term.get('queue_'+key,0.)))
    alpha=np.exp(-dt*c['thermal_ua']/c['thermal_c'])
    if opt['thermal']:
        prev=np.r_[.5 if init is None else init['cold'],df.cold.to_numpy()[:-1]]
        expected=alpha*prev+(1-alpha)/(c['thermal_ua']*c['thermal_range'])*(c['thermal_cop']*df.ac-df.it)+(1-alpha)*(c['thermal_tmax']-c['thermal_ambient'])/c['thermal_range']
        eq('ves_recursion',df.cold-expected)
        add('ves_terminal',abs(df.cold.iloc[-1]-(.5 if term is None else term.get('cold',.5)))-meta.get('thermal_terminal_slack',0.))
    else: eq('ves_disabled',df.ac)
    add('ves_bounds',max(-df.cold.min(),df.cold.max()-1,(df.ac-c['ac_max']).max()))
    duration=c['ride_h'] if opt['policy']=='worst' else min(c['reserve_h'],c['ride_h'])
    expected=[]
    for t in range(n):
        if opt['policy']=='fixed': expected.append(2.5); continue
        left=min(duration,n*dt); need=0.
        series=df.site.to_numpy() if opt['policy']!='static' else sum(df['load_'+k] for k in KEYS).to_numpy()
        for k in range(n):
            h=min(dt,left)
            need+=h*series[(t+1+k)%n]/eta; left-=h
            if left<=1e-12: break
        expected.append(need+(.5 if meta['modules']>=6 and not opt.get('physical_topology') else 0.))
    eq('reserve_floor_dynamic',df.floor-np.array(expected))
    add('reserve_commit',(df.floor-df.reserve_commit-df.reserve_slack).max())
    add('reserve_available',(df.reserve_commit-df.soc-df.reserve_slack).max())
    add('reg_power_up',(df.discharge+df.reg_up-p).max())
    add('reg_power_dn',(df.charge+df.reg_dn-min(p,c['charge_rate']*e)).max())
    add('reg_energy_up',(df.reserve_commit+df.reg_up*c['reg_duration']/eta-df.soc-df.reserve_slack).max())
    add('reg_energy_dn',(df.soc+df.reg_dn*c['reg_duration']*eta-c['soc_max']*e).max())
    corrected=c.get('accounting_version',3)==4
    regulated=float(np.sum(ec+ed)) if corrected else dt*c['activation_beta']*(df.reg_up+df.reg_dn).sum()
    throughput=dt*(1. if corrected else .5)*(df.charge+df.discharge).sum()+regulated
    eq('throughput',throughput-meta['throughput'])
    if 'calibration' in opt:
        depth,rates=map(np.asarray,opt['calibration'])
        scheduled=dt*.5*(df.charge+df.discharge).sum()
        if opt['degradation']=='legacy': deg=15.*throughput
        elif opt['degradation']=='shallow': deg=rates[0]*throughput
        else:
            knots=np.r_[0.,depth,12.]
            costs=(2 if corrected else 1)*knots*np.r_[rates[0],rates,rates[-1]]
            deg=e*np.interp(scheduled/e,knots,costs)+rates[0]*regulated
        add('degradation_reconcile',abs(deg-meta['deg_cost']),1e-2)
    cost=dt*np.dot(df.grid,df.price)+c['demand_rate']*max(df.grid.max(),(init or {}).get('peak',0.))+meta['deg_cost']-dt*(c['reg_capacity_price']+c['reg_mileage_price']*c['mileage_ratio']*c['performance'])*(df.reg_up+df.reg_dn).sum()+meta['penalty']+meta.get('service_cost',0.)
    add('objective_reconcile',abs(cost-meta['cost']),1e-2)
    add('certified_order',meta.get('lo',meta['cost'])-meta.get('hi',meta['cost']))
    if term is not None:
        add('terminal_soc',abs(df.soc.iloc[-1]-term['soc'])-meta['terminal_slack'])
    if opt.get('eens_limit') is not None:
        short=np.maximum(0.,df.site.to_numpy()*1.15*c['ride_h']-np.minimum(df.soc.to_numpy()*eta,p*c['ride_h']))
        add('stress_eens_budget',short.mean()-opt['eens_limit'])
    return pd.DataFrame(rows)

def verify_directory(path):
    rows=[]
    for meta_path in sorted(Path(path).glob('trace_*.json')):
        meta=json.loads(meta_path.read_text(encoding='utf-8'))
        df=pd.read_csv(meta_path.with_suffix('.csv'))
        chk=verify_trace(df,meta)
        if meta['options'].get('physical_topology') or meta['options'].get('service_contracts'):
            from research_verify import verify_research
            name=meta_path.stem.removeprefix('trace_')
            physical=pd.read_csv(Path(path)/f'physical_{name}.csv') if meta['options'].get('physical_topology') else None
            services=pd.read_csv(Path(path)/f'service_{name}.csv') if meta['options'].get('service_contracts') else None
            chk=pd.concat([chk,verify_research(df,meta,physical,services)],ignore_index=True)
        chk['case']=meta_path.stem; rows.append(chk)
    if not rows: raise ValueError('no exported traces found')
    manifest=Path(path)/'capability_manifest.json'
    if manifest.is_file():
        from research_verify import verify_capability
        def read_case(name):
            if Path(name).name!=name or name in ('.','..'):raise ValueError('invalid capability case name')
            result=json.loads((Path(path)/f'trace_{name}.json').read_text(encoding='utf-8'))
            result['trace']=pd.read_csv(Path(path)/f'trace_{name}.csv')
            for kind in ('physical','service'):
                file=Path(path)/f'{kind}_{name}.csv'
                if file.is_file():result[kind+'_trace']=pd.read_csv(file)
            return result
        for entry in json.loads(manifest.read_text(encoding='utf-8')):
            reference=read_case(entry['reference']);result=dict(entry['result'],witness=read_case(entry['witness']))
            chk=verify_capability(reference,result,entry['start'],entry['duration_slots']);chk['case']=entry['witness'];rows.append(chk)
    return pd.concat(rows,ignore_index=True)

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('run_dir'); a=ap.parse_args()
    checks=verify_directory(a.run_dir)
    print(f"Independent verification: {checks['pass'].sum()}/{len(checks)}")
    if not checks['pass'].all():
        print(checks[~checks['pass']].to_string(index=False)); raise SystemExit(1)
