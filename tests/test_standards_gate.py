import copy
import json
import pytest
from config import ROOT,Config,load_inputs,tariff
from standards import assess
from dispatch import solve
from energy_metrics import summarize

def test_no_standard_disappears_and_no_software_pass_becomes_conformity():
    ledger=json.loads((ROOT/'docs/standards/requirements.json').read_text(encoding='utf-8'))
    assert assess(ledger)['standards_status']=='BLOCKED'
    fake=copy.deepcopy(ledger)
    for row in fake['items']:
        row.update(review_status='verified',applicability='applicable',reviewer='fixture',applicability_reason='fixture',clause_refs=['fixture'])
    assert assess(fake)['standards_status']=='BLOCKED'
    fake['items'].pop()
    with pytest.raises(ValueError):assess(fake)

def test_modeled_energy_ratio_does_not_claim_facility_pue():
    r=solve(load_inputs().iloc[:4],tariff(4))
    m=summarize(r['trace'],Config())
    assert m['battery_loss_mwh']>0 and m['modeled_facility_to_it_ratio']>1
    assert not m['complete_facility_pue'] and not m['ashrae_compliance']
