"""Seal the final source, reports and references to independently sealed experiments."""
import json
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
import shutil
import pandas as pd
from config import ROOT
from runctx import RunContext,sha256,snapshot_source
from verify import verify_run

def main(test_report=None):
    run=RunContext(ROOT,prefix='v3delivery')
    snapshot_source(run.dir/'sim')
    for folder in ('docs','tests','baseline','data','tools'):
        dest=run.dir/folder; dest.mkdir()
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.csv','.md','.json','.xlsx'):
                target=dest/p.relative_to(ROOT/folder)
                target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,target)
    for name in ('README.md','requirements.txt','requirements-lock.txt','run_all.ps1','run_all.sh','pytest.ini'):
        run.freeze_input(ROOT/name)
    tests={'status':'not_recorded'}
    if test_report:
        report=Path(test_report).resolve()
        suites=list(ET.parse(report).getroot().iter('testsuite'))
        tests={key:sum(int(s.get(key,0)) for s in suites)
               for key in ('tests','failures','errors','skipped')}
        if tests['failures'] or tests['errors']: raise AssertionError('test report contains failures')
        tests['status']='passed'; run.freeze_input(report)
    rows=[]; refs=[]
    for stage in range(4):
        published=json.loads((ROOT/f'results/v3_stage{stage}/PUBLISHED.json').read_text(encoding='utf-8'))
        path=ROOT/'runs'/published['latest_run']
        if verify_run(path): raise AssertionError(f'stage {stage} failed seal verification')
        rows.append(pd.read_csv(path/'checks_v3.csv'))
        refs.append(dict(stage=stage,run=str(path.relative_to(ROOT)),seal_sha256=sha256(path/'SEAL.json')))
    pd.concat(rows,ignore_index=True).to_csv(run.dir/'checks_v3.csv',index=False)
    manifest=dict(stages=refs,regression=json.loads((ROOT/'baseline/v3_regression_evidence.json').read_text()),
                  replay=json.loads((ROOT/'evidence/replay_verification.json').read_text()) if (ROOT/'evidence/replay_verification.json').is_file() else None,
                  optional_mpc_metrics=pd.read_csv(ROOT/'results/mpc_v3/execution_metrics.csv').to_dict(orient='records') if (ROOT/'results/mpc_v3/execution_metrics.csv').is_file() else [],
                  tests=tests,
                  research_acceptance='conditional: see docs/路线图落实清单_v3.md')
    (run.dir/'delivery_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    run.write_config(dict(version=3,scope='research software delivery'))
    run.write_env(); run.write_provenance(); run.seal()
    (ROOT/'results/summary').mkdir(parents=True,exist_ok=True)
    shutil.copy2(run.dir/'delivery_manifest.json',ROOT/'results/summary/V3_DELIVERY.json')
    print(f'Final delivery: {run.dir}')
    if verify_run(run.dir): raise AssertionError('final delivery seal failed')

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--test-report'); args=ap.parse_args()
    main(args.test_report)
