"""Offline empirical-data analysis only. Does not modify sim or frozen inputs."""
import importlib
import io
import json
from pathlib import Path
import pickle
import hashlib
import re
from collections import Counter
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]


class DataOnlyUnpickler(pickle.Unpickler):
    """Only named NumPy/pandas data constructors, never arbitrary pickle globals."""
    allowed={('numpy.core.multiarray','_reconstruct'),('numpy','ndarray'),('numpy','dtype'),
        ('numpy.core.multiarray','scalar'),('numpy._core.multiarray','_reconstruct'),
        ('numpy._core.multiarray','scalar'),('pandas.core.frame','DataFrame'),
        ('pandas.core.internals.managers','BlockManager'),('pandas._libs.internals','_unpickle_block'),
        ('pandas.core.indexes.base','_new_Index'),('pandas.core.indexes.base','Index'),
        ('pandas.core.indexes.range','RangeIndex'),('builtins','slice')}
    def find_class(self,module,name):
        if (module,name) not in self.allowed:raise pickle.UnpicklingError('blocked pickle global: '+module+'.'+name)
        return getattr(importlib.import_module(module),name)
    def persistent_load(self,pid):raise pickle.UnpicklingError('persistent objects not allowed')


def read_hust(path):
    path=Path(path)
    if path.stat().st_size>100_000_000:raise ValueError('cell exceeds offline memory budget')
    value=DataOnlyUnpickler(io.BytesIO(path.read_bytes())).load()
    if not isinstance(value,dict) or len(value)!=1:raise ValueError('unexpected HUST schema')
    return next(iter(value.items()))


def cycle_observation(frame,capacity_mah):
    t=frame['Time (s)'].to_numpy(float);v=frame['Voltage (V)'].to_numpy(float);a=np.abs(frame['Current (mA)'].to_numpy(float))/1000
    if len(t)<2 or not np.isfinite(np.r_[t,v,a,capacity_mah]).all() or (np.diff(t)<0).any() or (v<0).any() or capacity_mah<=0:
        raise ValueError('invalid cell samples or units')
    status=frame.Status.astype(str).str.lower().to_numpy();dis=np.array(['discharge' in s for s in status]);ch=np.array(['charge' in s and 'discharge' not in s for s in status])
    h=np.diff(t)/3600.;same=status[1:]==status[:-1];power=(a[1:]*v[1:]+a[:-1]*v[:-1])/2
    cm=same&ch[1:]&ch[:-1];dm=same&dis[1:]&dis[:-1]
    ec=float(np.sum(power[cm]*h[cm]));ed=float(np.sum(power[dm]*h[dm]))
    if ec<=0 or ed<=0:raise ValueError('missing charge or discharge phase')
    missing=~(cm|dm);gap=float(np.sum(h[missing])*np.max(a)*np.max(v))
    qdis=float(np.sum((a[1:]+a[:-1])[dm]/2*h[dm]))
    return dict(capacity_ah=float(capacity_mah/1000),charge_wh_observed=ec,discharge_wh_observed=ed,
        missing_transition_energy_bound_wh=gap,dc_roundtrip_observed=ed/ec,
        dc_roundtrip_lower=ed/(ec+gap),dc_roundtrip_upper=(ed+gap)/ec,
        discharge_integral_relative_error=abs(qdis-capacity_mah/1000)/(capacity_mah/1000),
        max_discharge_c=float(a[dis].max()/1.1),max_voltage=float(v.max()),min_voltage=float(v.min()))


def battery_observations(folder):
    tables=[]
    for file in ('1-1.pkl','1-2.pkl'):
        cell,data=read_hust(Path(folder)/file);rows=[]
        if set(data['dq'])!=set(data['data']):raise ValueError('capacity/cycle IDs disagree')
        for cycle in sorted(data['dq']):rows.append(dict(cell=cell,cycle=int(cycle),**cycle_observation(data['data'][cycle],data['dq'][cycle])))
        table=pd.DataFrame(rows)
        table['observed_dc_wh_efc']=(table.charge_wh_observed+table.discharge_wh_observed).cumsum()/(2*1.1*3.3)
        table['dc_wh_efc_upper']=table.observed_dc_wh_efc+table.missing_transition_energy_bound_wh.cumsum()/(2*1.1*3.3)
        q0=float(table.capacity_ah.iloc[:10].median());table['initial_capacity_reference_ah']=q0
        table['relative_capacity_retention']=table.capacity_ah/q0
        tables.append(table)
    return pd.concat(tables,ignore_index=True)


def fit_retention(table,train_fraction=.6):
    train_cell=table[table.cell=='1-1'].sort_values('cycle');cut=int(len(train_cell)*train_fraction)
    train=train_cell.iloc[10:cut];test=train_cell.iloc[cut:]
    if len(train)<10 or test.empty:raise ValueError('insufficient chronological split')
    slope,intercept=np.polyfit(train.observed_dc_wh_efc,train.relative_capacity_retention,1)
    base_slope=-.00003;base_intercept=float((train.relative_capacity_retention-base_slope*train.observed_dc_wh_efc).mean())
    def metrics(d):
        true=d.relative_capacity_retention.to_numpy();x=d.observed_dc_wh_efc.to_numpy()
        return dict(n=len(d),rmse=float(np.sqrt(np.mean((intercept+slope*x-true)**2))),
            frozen_proxy_rmse=float(np.sqrt(np.mean((base_intercept+base_slope*x-true)**2))))
    other=table[table.cell=='1-2'].sort_values('cycle').iloc[10:]
    return dict(train_cell='1-1',first_training_cycle=int(train.cycle.iloc[0]),last_training_cycle=int(train.cycle.iloc[-1]),
        loss_per_observed_dc_wh_efc=float(-slope),intercept=float(intercept),
        units='relative capacity loss per recorded DC nominal-Wh equivalent full cycle',
        chronological_holdout=metrics(test),other_protocol_cell_holdout=metrics(other),
        transfer_status='external accelerated-cell prior only; not a UPS pack parameter',
        identifiability='cycle/calendar contributions and DoD dependence not separately identified',
        leakage_control='first 10 cycles normalize each cell; fit uses only first 60% of cell 1-1; other cell after first 10 never fitted')


def arrival_observations(path,chunksize=200000):
    groups=[];n=0;earliest=latest=None;timezone=None;hist={key:Counter() for key in ('ContextTokens','GeneratedTokens')}
    for data in pd.read_csv(path,chunksize=chunksize):
        if list(data.columns)!=['TIMESTAMP','ContextTokens','GeneratedTokens']:raise ValueError('unexpected arrival schema')
        t=pd.to_datetime(data.TIMESTAMP,format='mixed',errors='raise')
        tz=str(t.dt.tz) if t.dt.tz is not None else 'unspecified in source; not assumed UTC'
        if n and timezone!=tz:raise ValueError('inconsistent source timezone')
        timezone=tz
        numeric=data[['ContextTokens','GeneratedTokens']].to_numpy(float)
        if not np.isfinite(numeric).all() or (numeric<0).any() or not np.equal(numeric,np.floor(numeric)).all():raise ValueError('invalid token counts')
        for key in hist:
            values,counts=np.unique(data[key],return_counts=True);hist[key].update(dict(zip(map(int,values),map(int,counts))))
        frame=pd.DataFrame({'minute':t.dt.floor('min'),'requests':1,'context_tokens':data.ContextTokens,'generated_tokens':data.GeneratedTokens})
        groups.append(frame.groupby('minute').sum());n+=len(data)
        earliest=t.min() if earliest is None else min(earliest,t.min());latest=t.max() if latest is None else max(latest,t.max())
    if not n:raise ValueError('empty arrival data')
    bins=pd.concat(groups).groupby(level=0).sum().reindex(pd.date_range(earliest.floor('min'),latest.floor('min'),freq='min'),fill_value=0)
    bins.index.name='minute';bins['complete_minute']=(bins.index>=earliest.ceil('min'))&(bins.index+pd.Timedelta(minutes=1)<=latest)
    bins['requests_per_second']=bins.requests/60.
    complete=bins[bins.complete_minute]
    split=int(.7*len(complete));train=complete.iloc[:split];test=complete.iloc[split:]
    if len(train)<2 or test.empty:raise ValueError('insufficient complete arrival windows')
    counts=complete.requests.to_numpy();mu=float(train.requests.mean())
    def quantile(counter,q):
        values=np.array(sorted(counter));c=np.cumsum([counter[v] for v in values]);return int(values[np.searchsorted(c,q*c[-1],side='left')])
    summary=dict(requests=n,first_timestamp=str(earliest),last_timestamp=str(latest),timezone=timezone,
        observed_span_seconds=float((latest-earliest).total_seconds()),complete_minutes=len(complete),
        train_minutes=len(train),holdout_minutes=len(test),train_mean_requests_per_second=mu/60,
        holdout_mean_requests_per_second=float(test.requests.mean()/60),
        constant_rate_holdout_rmse_requests_per_minute=float(np.sqrt(np.mean((test.requests-mu)**2))),
        count_fano_60s=float(np.var(counts,ddof=1)/np.mean(counts)) if np.mean(counts)>0 else None,
        token_quantiles={key:{f'p{int(q*100)}':quantile(counter,q) for q in (.5,.9,.99)} for key,counter in hist.items()},
        identifies=['arrival count profiles','input/output token length distributions'],
        does_not_identify=['response-time SLA','service rate','watts','joules/request','deadlines'])
    if int(bins.requests.sum())!=n:raise AssertionError('request count not conserved')
    return bins.reset_index(),summary


def benchmark_observations(folder):
    rows=json.loads((Path(folder)/'summary_results.json').read_text(encoding='utf-8'))
    selected=[r for r in rows if r.get('has_power') and r.get('compliance')==1 and r.get('errors')==0 and 'llama' in r.get('Model','').lower()]
    records=[]
    for row in selected:
        if row.get('Power_Units')!='Watts' or row.get('Performance_Units')!='Tokens/s':raise ValueError('benchmark units changed')
        records.append(dict(model=row['Model'],scenario=row['Scenario'],hardware=row['Platform'],submission=row['ID'],
            nodes=row['Nodes'],watts=row['Power_Result'],tokens_per_second=row['Performance_Result'],
            gross_joules_per_output_token=row['Power_Result']/row['Performance_Result'],location=row['Location']))
    text=(Path(folder)/'mlperf_log_summary.txt').read_text(encoding='utf-8')
    def value(label):
        m=re.search(r'^'+re.escape(label)+r'\s*:\s*([0-9.eE+-]+)',text,re.M)
        if not m:raise ValueError('missing benchmark statistic: '+label)
        return float(m.group(1))
    anchor=next(r for r in selected if r['Model']=='llama2-70b-99' and r['Scenario']=='Server')
    qps=value('Completed samples per second');tokens=value('Completed tokens per second')
    if abs(tokens-anchor['Performance_Result'])>1.:raise ValueError('benchmark table and log disagree')
    summary=dict(model=anchor['Model'],hardware=anchor['Platform'],measured_average_watts=anchor['Power_Result'],
        completed_requests_per_second=qps,gross_joules_per_request=anchor['Power_Result']/qps,
        response_p99_s=value('99.00 percentile latency (ns)')/1e9,
        ttft_p99_s=value('99.00 percentile first token latency (ns)')/1e9,
        tpot_p99_s=value('99.00 percentile time to output token (ns)')/1e9,
        benchmark_ttft_target_s=value('ttft_latency (ns)')/1e9,benchmark_tpot_target_s=value('tpot_latency (ns)')/1e9,
        assumption='active whole-system average, not GPU TDP or marginal request energy',
        not_identifiable=['idle intercept','marginal joules/request','target-site SLO','site PUE'],
        sla_warning='TTFT, TPOT and complete response are different metrics; 0.2s proxy is not validated')
    return pd.DataFrame(records),summary


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1<<20),b''):h.update(block)
    return h.hexdigest()


def analyze():
    base=ROOT/'external/empirical_20261111';manifest=json.loads((base/'acquisition.json').read_text(encoding='utf-8'))
    for row in manifest['records']:
        if sha256(ROOT/row['path'])!=row['sha256']:raise ValueError('acquisition hash mismatch')
    week=json.loads((base/'azure/week_acquisition.json').read_text(encoding='utf-8'))
    if week.get('status')!='downloaded_complete' or sha256(ROOT/week['path'])!=week['sha256']:raise ValueError('Azure week incomplete or changed')
    out=ROOT/'data/empirical/20261111';out.mkdir(parents=True,exist_ok=True)
    battery=battery_observations(base/'hust');battery.to_csv(out/'hust_two_cells.csv',index=False,float_format='%.12g',lineterminator='\n')
    fit=fit_retention(battery)
    arrival={}
    for label,name in [('code_2023','AzureLLMInferenceTrace_code.csv'),('conv_2023','AzureLLMInferenceTrace_conv.csv'),('code_2024','AzureLLMInferenceTrace_code_1week.csv')]:
        bins,stats=arrival_observations(base/'azure'/name);bins.to_csv(out/f'azure_{label}_minute.csv',index=False,float_format='%.12g',lineterminator='\n');arrival[label]=stats
    benchmark,point=benchmark_observations(base/'mlperf');benchmark.to_csv(out/'mlperf_llm_power.csv',index=False,float_format='%.12g',lineterminator='\n')
    cells=[]
    for cell,d in battery.groupby('cell'):
        cells.append(dict(cell=cell,cycles=len(d),initial_capacity_ah=float(d.initial_capacity_reference_ah.iloc[0]),
            last_capacity_ah=float(d.capacity_ah.iloc[-1]),last_nominal_capacity_fraction=float(d.capacity_ah.iloc[-1]/1.1),
            dc_roundtrip_median=float(d.dc_roundtrip_observed.iloc[10:].median()),
            median_transition_lower=float(d.dc_roundtrip_lower.iloc[10:].median()),median_transition_upper=float(d.dc_roundtrip_upper.iloc[10:].median()),
            max_integral_relative_error=float(d.discharge_integral_relative_error.max()),max_discharge_c=float(d.max_discharge_c.max())))
    summary=dict(date='2026-11-11',scope='external measured data; no target-site validation or default parameter adoption',
        core_changed=False,default_inputs_changed=False,battery=dict(cells=cells,fit=fit,conditions='two selected LFP/graphite 1.1Ah/3.3V cells, 30C; multistage accelerated cycling; no shallow-UPS or calendar separation'),
        arrivals=arrival,benchmark=point,
        curated_files=[dict(path=str(p.relative_to(ROOT)),sha256=sha256(p),size=p.stat().st_size) for p in sorted(out.glob('*.csv'))],
        acquisition=manifest,azure_week_acquisition=week)
    folder=ROOT/'docs/research/calibration';folder.mkdir(parents=True,exist_ok=True)
    (folder/'external_calibration_evidence.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(dict(cells=cells,fit=fit,arrivals=arrival,benchmark=point),ensure_ascii=True,indent=2),flush=True)
    return summary

if __name__=='__main__':analyze()
