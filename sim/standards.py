"""Fail-closed evidence-completeness gate, separate from software acceptance.
Does not infer engineering certification from unit tests or component counts.
"""
import argparse
import hashlib
import json
from pathlib import Path
from config import ROOT

IDS={f'STD-{i:02d}' for i in range(1,12)}

def assess(ledger,root=ROOT):
    items=ledger.get('items',[]);ids=[row.get('id') for row in items]
    if len(ids)!=11 or set(ids)!=IDS:raise ValueError('all 11 unique user standards must be retained')
    results=[]
    for item in items:
        reasons=[]
        for key in ('adopted_version','reviewer','applicability_reason'):
            if not item.get(key):reasons.append('missing '+key)
        if item.get('applicability') not in ('applicable','not_applicable'):reasons.append('applicability not reviewed')
        if item.get('applicability')!='not_applicable' and not item.get('clause_refs'):reasons.append('accurate clauses not verified')
        if item.get('review_status')!='verified':reasons.append('clause review pending')
        proofs=item.get('evidence',[])
        if not proofs:reasons.append('no verified clause/test/external evidence')
        kinds=set()
        for proof in proofs:
            path=Path(proof.get('path',''))
            if not path.is_absolute():path=Path(root)/path
            digest=proof.get('sha256','')
            if not path.is_file() or len(digest)!=64 or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                reasons.append('missing or changed evidence')
            kinds.add(proof.get('kind'))
        required={'applicability_review'} if item.get('applicability')=='not_applicable' else {'clause_review','independent_test'}|({'external_engineering'} if item.get('external_required',True) else set())
        if not required<=kinds:reasons.append('evidence categories incomplete')
        results.append(dict(id=item['id'],status='BLOCKED' if reasons else 'EVIDENCE_COMPLETE',reasons=reasons,
                            model_support=item.get('model_support',[])))
    return dict(standards_status='BLOCKED' if any(row['reasons'] for row in results) else 'EVIDENCE_COMPLETE',
                certification=False,meaning='evidence completeness only; no automatic engineering certification',items=results)

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--ledger',type=Path,default=ROOT/'docs/standards/requirements.json');args=ap.parse_args()
    report=assess(json.loads(args.ledger.read_text(encoding='utf-8')))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    raise SystemExit(0 if report['standards_status']=='EVIDENCE_COMPLETE' else 2)
