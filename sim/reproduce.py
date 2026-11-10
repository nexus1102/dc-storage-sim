"""Replay a sealed stage using its frozen Python sources and inputs in a new root."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from runctx import sha256
from verify import verify_run

def replay(run_dir,destination):
    run_dir=Path(run_dir).resolve(); destination=Path(destination).resolve()
    if verify_run(run_dir): raise ValueError('unverified source run')
    if destination.exists(): raise FileExistsError('replay destination must be new')
    destination.mkdir(parents=True)
    modern=(run_dir/'source/source_layout.json').is_file()
    inputs=destination/'data/inputs' if modern else destination/'results'
    raw=destination/'data/raw' if modern else destination/'data'
    inputs.mkdir(parents=True); (destination/'docs').mkdir()
    for name in ('load_business_24h.csv','degradation_calibration.csv'):
        shutil.copy2(run_dir/'inputs'/name,inputs/name)
    shutil.copy2(run_dir/'inputs/parameter_provenance.csv',destination/'docs/parameter_provenance.csv')
    for name in ('验收规范与证据矩阵.md','requirements.json'):
        if (run_dir/'inputs'/name).is_file():
            (destination/'docs/standards').mkdir(exist_ok=True)
            shutil.copy2(run_dir/'inputs'/name,destination/'docs/standards'/name)
    if (run_dir/'inputs/requirements-lock.txt').is_file():
        shutil.copy2(run_dir/'inputs/requirements-lock.txt',destination/'requirements-lock.txt')
    for name in ('openb_pod_list_default.csv','openb_pod_list_cpu050.csv'):
        if (run_dir/'inputs'/name).is_file():
            raw.mkdir(parents=True,exist_ok=True)
            shutil.copy2(run_dir/'inputs'/name,raw/name)
    if modern and (run_dir/'inputs/v2_reference.csv').is_file():
        (destination/'baseline').mkdir()
        shutil.copy2(run_dir/'inputs/v2_reference.csv',destination/'baseline/v2_reference.csv')
    if (run_dir/'inputs/results_v2.csv').is_file():
        shutil.copy2(run_dir/'inputs/results_v2.csv',destination/'results/results_v2.csv')
    c=json.loads((run_dir/'run_config.json').read_text(encoding='utf-8'))
    env=os.environ.copy(); env['DC_STORAGE_ROOT']=str(destination)
    # Legacy PyPSA scripts predate DC_STORAGE_ROOT. Point their historical CSV
    # lookup to the frozen replay root without editing any sealed source.
    legacy_fix='' if modern else f"import pipeline_v2;pipeline_v2.ROOT=Path({str(destination)!r});"
    if c.get('suite')=='thesis':
        entry='thesis_experiments.py';arguments=['--slots',str(c['slots'])]
    elif 'stage' in c:
        entry='experiments.py';arguments=['--stage',str(c['stage']),'--scenarios',str(c['n_scen'])]
    elif 'slots' in c and 'contracts' in c:
        entry='foundation_experiments.py';arguments=['--slots',str(c['slots']),'--reference',c.get('reference_mode','economic')]
    else:raise ValueError('unsupported sealed run configuration')
    bootstrap=(f"import sys,runpy;from pathlib import Path;sys.path.insert(0,{str(run_dir/'source')!r});"
               +legacy_fix+
               f"runpy.run_path({str(run_dir/'source'/entry)!r},run_name='__main__')")
    subprocess.run([sys.executable,'-c',bootstrap,*arguments],env=env,check=True)
    fresh=next((destination/'runs').iterdir())
    records=[]
    for path in run_dir.glob('*.csv'):
        if path.name=='provenance.csv': continue
        counterpart=fresh/path.name
        match=counterpart.is_file() and sha256(path)==sha256(counterpart)
        records.append(dict(file=path.name,sha256=sha256(path),match=match))
    (destination/'replay_comparison.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    if not records or not all(r['match'] for r in records): raise AssertionError('replay numeric CSV hash drift')
    print(f'Replay matched {len(records)} CSV hashes')
    return fresh

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('run_dir'); ap.add_argument('destination'); a=ap.parse_args()
    replay(a.run_dir,a.destination)
