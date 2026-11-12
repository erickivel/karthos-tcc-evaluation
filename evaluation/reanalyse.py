"""Read-only historical analysis: python -m evaluation.reanalyse SOURCE OUTPUT.

OUTPUT must not exist and must be outside SOURCE. No historical report is rewritten.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from .scoring import evaluate_window, read, PROTOCOL_VERSION, VERIFIER_VERSION, ERROR_POLICY_VERSION


def hashes(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()}


def reanalyse(source, destination, *, protocol_version=None):
    if protocol_version not in (None,3,4):raise ValueError("unsupported reanalysis protocol")
    source=Path(source).resolve(); destination=Path(destination).resolve()
    if not source.is_dir(): raise ValueError('source evidence directory does not exist')
    if destination.exists(): raise ValueError('analysis destination must be new')
    if destination==source or source in destination.parents or destination in source.parents:
        raise ValueError('analysis destination must be outside source evidence')
    before=hashes(source)
    thresholds=read(source/'frozen-thresholds.json',{})
    if (source/'manifest.json').exists(): windows=[source]
    else: windows=sorted(p.parent for p in source.glob('measured/*/windows/*/manifest.json'))
    if not windows: raise ValueError('no measured windows found')
    reports=[]
    for window in windows:
        result=evaluate_window(window,thresholds,protocol_version=protocol_version)
        result['analysis_kind']='historical_reanalysis'
        result['new_protocol_evidence']={
            'state':'incomplete',
            'reason':'historical evidence cannot establish new quiet-counter and exact-query boundary requirements'
        } if not (window/'vm-parity/manifest.json').exists() or not (window/'quiet-before.json').exists() else {'state':'available'}
        if protocol_version==4:
            required=('structured/status.json','structured/agent/raw/receiver-input.ndjson',
                      'structured/agent/raw/metrics.json','structured/gateway/raw/metrics.json','structured/gateway/raw/traces.json')
            missing=[name for name in required if not (window/name).is_file() or not (window/name).stat().st_size]
            result['new_protocol_evidence']=dict(state='incomplete' if missing else 'available',missing=missing,
                reason='Historical absence is retained; exact receiver inputs and full structured capture cannot be reconstructed from later observations')
            result.update(protocol_version=4,verifier_version='4.0.0')
        reports.append((window,result))
    if hashes(source)!=before:
        raise RuntimeError('source evidence changed during analysis; refusing to publish')
    destination.mkdir(parents=True)
    for window,result in reports:
        relative=window.relative_to(source) if window!=source else Path('window')
        target=destination/relative/'verification.json';target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    code_root=Path(__file__).parent
    provenance=dict(protocol_version=protocol_version or PROTOCOL_VERSION,verifier_version='4.0.0' if protocol_version==4 else VERIFIER_VERSION,
                    error_policy_version=ERROR_POLICY_VERSION,source_directory=str(source),
                    source_sha256=before,verifier_sha256={name:hashlib.sha256((code_root/name).read_bytes()).hexdigest() for name in ('scoring.py','series_identity.py','reanalyse.py','verification_v4.py','evidence_v4.py')},
                    reports=[dict(window=str(w.relative_to(source)),state=r['state'],oracle_valid=r['oracle_valid']) for w,r in reports])
    from .pilot import resume_fingerprint
    provenance['harness_fingerprint']=resume_fingerprint()
    (destination/'analysis-manifest.json').write_text(json.dumps(provenance,indent=2,sort_keys=True)+'\n')
    return provenance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--protocol',type=int,choices=(3,4))
    args=parser.parse_args();report=reanalyse(args.source,args.output,protocol_version=args.protocol)
    print(json.dumps(dict(output=str(args.output),windows=len(report['reports']),verifier_version=report['verifier_version'])))


if __name__=='__main__': main()
