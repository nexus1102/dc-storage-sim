from dataclasses import replace
import pytest
from config import Config
from physical_topology import architecture,Fault,Topology,Unit,PowerPath,LoadGroup
from outage import replay,recharge

def test_three_intervals_and_seconds_transfer_not_rounded():
    a=architecture('N+1',energy=5.);cfg=Config();soc=[4.75]*6
    r=replay(a,soc,[2.,3.,4.],.75,cfg,Fault('grid'))
    assert r['interval_h']==[.25]*3 and r['service_preserved']
    assert sum(soc)-sum(r['soc_end'])==pytest.approx((2+3+4)*.25/.95)
    delayed=replay(a,soc,[2.,3.,4.],.75,cfg,Fault('grid'),transfer_s=.01)
    assert delayed['eens_mwh']==pytest.approx(2*.01/3600.)

def test_power_energy_shortages_not_double_counted():
    a=architecture('N',n=1,energy=1.,power=.5)
    r=replay(a,[.1],[100.],1.,replace(Config(),dt=1.),Fault('grid'))
    assert r['eens_mwh']==pytest.approx(99.905) and not r['service_preserved']

def test_path_maintenance_and_generator_failure():
    cfg=Config();single=architecture('N+1');dual=architecture('2(N+1)');fault=Fault('maintenance_A',paths=('A',))
    assert not replay(single,[.475]*6,[2.],.25,cfg,fault)['service_preserved']
    assert replay(dual,[.475]*12,[2.],.25,cfg,fault)['service_preserved']
    failed=replay(single,[.1]*6,[2.,2.,2.],.75,cfg,Fault('grid'))
    started=replay(single,[.1]*6,[2.,2.,2.],.75,cfg,Fault('grid'),generator_ready_s=20.)
    assert failed['eens_mwh']>0 and started['service_preserved']

def test_shared_spare_static_route_and_recharge_failure():
    a=Topology('shared',(Unit('spare',('A','B'),1.,2.),),(PowerPath('A',2.),PowerPath('B',2.)),(LoadGroup('a',('A',),.5),LoadGroup('b',('B',),.5)))
    r=replay(a,[.95],[1.],.25,Config(),Fault('grid'))
    assert r['eens_mwh']==pytest.approx(.25)
    b=architecture('N',n=1);rec=recharge(b,[.1],[.475],[1.]*8,Config())
    assert rec['recovered'] and rec['soc_end'][0]==pytest.approx(.475)
    assert not recharge(b,[.1],[.475],[20.],Config())['recovered']
