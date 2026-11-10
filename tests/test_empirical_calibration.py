"""Offline data units, chronological leakage and ingestion integrity checks."""
import io
import json
from pathlib import Path
import pickle
import sys
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from empirical_analysis import DataOnlyUnpickler,cycle_observation,fit_retention,arrival_observations,benchmark_observations
from acquire_calibration_data import record_file,byte_range,SESSION


def test_cell_ma_seconds_to_ah_and_wh():
    frame=pd.DataFrame({'Time (s)':[0,3600,3600,7200], 'Voltage (V)':[3.]*4,
        'Current (mA)':[1000,1000,-1000,-1000], 'Status':['charge','charge','discharge','discharge']})
    r=cycle_observation(frame,1000.)
    assert r['capacity_ah']==1. and r['charge_wh_observed']==3. and r['discharge_wh_observed']==3.
    assert r['dc_roundtrip_observed']==1. and r['discharge_integral_relative_error']==0.
    frame.loc[2,'Time (s)']=3500
    with pytest.raises(ValueError):cycle_observation(frame,1000.)


def test_pickle_does_not_execute_arbitrary_globals():
    class Payload:
        def __reduce__(self):return eval,('1+1',)
    with pytest.raises(pickle.UnpicklingError):DataOnlyUnpickler(io.BytesIO(pickle.dumps(Payload()))).load()


def test_holdout_labels_cannot_change_fitted_slope():
    data=pd.DataFrame([dict(cell=cell,cycle=i,observed_dc_wh_efc=float(i),relative_capacity_retention=1-.0001*i) for cell in ('1-1','1-2') for i in range(1,101)])
    a=fit_retention(data)
    assert a['loss_per_observed_dc_wh_efc']==pytest.approx(.0001)
    data.loc[(data.cell=='1-2')|(data.cycle>60),'relative_capacity_retention']=.1
    b=fit_retention(data)
    assert a['loss_per_observed_dc_wh_efc']==b['loss_per_observed_dc_wh_efc']
    assert a['intercept']==b['intercept'] and b['chronological_holdout']['rmse']>.5


@pytest.mark.parametrize('zone',['','+00:00'])
def test_arrivals_preserve_zero_bins_duplicates_and_chunk_boundaries(tmp_path,zone):
    file=tmp_path/'trace.csv'
    pd.DataFrame({'TIMESTAMP':['2024-01-01 00:'+x+zone for x in ('00:10','01:00','01:00','02:00','04:59','05:30')],
        'ContextTokens':[10,10,20,20,30,30],'GeneratedTokens':[1,2,3,4,5,6]}).to_csv(file,index=False)
    bins,a=arrival_observations(file,chunksize=2);other,b=arrival_observations(file,chunksize=100)
    pd.testing.assert_frame_equal(bins,other);assert a==b
    assert a['timezone']==('UTC' if zone else 'unspecified in source; not assumed UTC')
    assert bins.requests.sum()==6 and bins.loc[3,'requests']==0
    assert not bins.complete_minute.iloc[0] and not bins.complete_minute.iloc[-1]
    assert a['train_mean_requests_per_second']==pytest.approx(1.5/60)
    assert a['holdout_mean_requests_per_second']==pytest.approx(.5/60)


def test_benchmark_token_rate_is_not_request_rate(tmp_path):
    row=dict(has_power=True,compliance=1,errors=0,Model='llama2-70b-99',Scenario='Server',Power_Units='Watts',Performance_Units='Tokens/s',Power_Result=120.,Performance_Result=60.,Platform='fixture',ID='fixture',Nodes=1,Location='fixture')
    (tmp_path/'summary_results.json').write_text(json.dumps([row]))
    (tmp_path/'mlperf_log_summary.txt').write_text('Completed samples per second : 2\nCompleted tokens per second : 60\n99.00 percentile latency (ns) : 1000000000\n99.00 percentile first token latency (ns) : 100000000\n99.00 percentile time to output token (ns) : 50000000\nttft_latency (ns): 2000000000\ntpot_latency (ns): 200000000\n')
    _,r=benchmark_observations(tmp_path)
    assert r['gross_joules_per_request']==60. and r['response_p99_s']==1.
    assert r['ttft_p99_s']==.1 and r['tpot_p99_s']==.05


def test_acquisition_does_not_overwrite_different_bytes(tmp_path):
    # Supply a path below the repository so relative provenance is meaningful.
    import acquire_calibration_data as acquisition
    old=acquisition.ROOT;acquisition.ROOT=tmp_path
    try:
        p=tmp_path/'data.bin';record_file(p,b'first','https://example.test/data')
        with pytest.raises(ValueError):record_file(p,b'changed','https://example.test/data')
        assert p.read_bytes()==b'first'
    finally:acquisition.ROOT=old


def test_full_archive_response_is_rejected(monkeypatch):
    class Response:
        status_code=200;headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(SESSION,'get',lambda *a,**k:Response())
    with pytest.raises(ValueError,match='honor'):byte_range('https://example.test/archive',0,22)
