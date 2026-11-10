"""Dependency-boundary checks: V3 must work without old outputs or archive files."""
import json
from pathlib import Path
from config import ROOT,INPUTS,RAW_DATA,BASELINE,load_inputs
from degradation import calibration
from runctx import snapshot_source,RunContext

def test_inputs_are_separate_from_outputs():
    assert INPUTS==ROOT/'data/inputs'
    assert RAW_DATA==ROOT/'data/raw'
    assert len(load_inputs())==96
    assert len(calibration()[0])==3
    assert (BASELINE/'v2_reference.csv').is_file()

def test_snapshots_include_reference_package(tmp_path):
    target=tmp_path/'source'
    snapshot_source(target)
    assert (target/'reference/pipeline_v2.py').is_file()
    assert (target/'reference/business_v2.py').is_file()
    assert json.loads((target/'source_layout.json').read_text())['layout_version']==2
    assert not (target/'pipeline_v1.py').exists()

def test_v3_source_does_not_import_archive():
    for file in (ROOT/'sim').rglob('*.py'):
        if '__pycache__' in file.parts: continue
        for line in file.read_text(encoding='utf-8').splitlines():
            assert not line.strip().startswith(('from archive','import archive'))

def test_publish_creates_missing_results_parent(tmp_path):
    run=RunContext(tmp_path,prefix='layout')
    (run.dir/'result.csv').write_text('value\n1\n',encoding='utf-8')
    run.publish(['result.csv'],tmp_path/'results/stage0')
    assert (tmp_path/'results/stage0/PUBLISHED.json').is_file()
