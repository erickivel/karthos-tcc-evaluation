"""Bounded live validation of generator file export on a retained owned demo."""
import argparse
import json
import time
from pathlib import Path

from . import runner as r
from .pilot import run_window, install_scripts, resume_fingerprint
from .protocol import Cell
from .manifests import OWNER_LABEL, OWNER_VALUE
from .generator_artifacts import validate_export


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    args=parser.parse_args()
    if Path(args.run_id).name!=args.run_id or args.run_id in ('.','..'):
        raise ValueError('run ID must be one directory name')
    cell=Cell('boutique','mesh',1)
    report=dict(kind='bounded artifact export diagnostic',state='starting',
                run_id=args.run_id,started_at=r.utc(),windows=[],
                budget_seconds=3600,artifact_recovery_seconds=r.CONFIG['artifact_recovery_seconds'])
    root=r.OUT/args.run_id
    root.mkdir(parents=True,exist_ok=False)
    r.write(root/'diagnostic-report.json',report)
    r._DEADLINE=time.monotonic()+3600
    try:
        namespace=r.kube_json('get','namespace',cell.namespace)
        if namespace['metadata'].get('labels',{}).get(OWNER_LABEL)!=OWNER_VALUE:
            raise RuntimeError('diagnostic requires the retained evaluation-owned Boutique namespace')
        if not r.ownership():raise RuntimeError('retained Karthos deployment is not evaluation-owned')
        jobs=r.kube_json('get','jobs','-n',cell.namespace)
        if any(j.get('status',{}).get('active',0) for j in jobs.get('items',[])):
            raise RuntimeError('active generator Job exists; finish its recovery/cleanup first')
        report['preflight']=r.preflight()
        r.write(root/'locked-config.json',r.CONFIG)
        r.write(root/'locked-harness.json',resume_fingerprint())
        r.shutil.copytree(r.ROOT/'evaluation',root/'locked-harness',ignore=r.shutil.ignore_patterns('__pycache__'))
        install_scripts(cell)
        parent=root/'measured'/cell.slug
        parent.mkdir(parents=True)
        specs=[dict(name='k6-export-smoke',generator='k6',duration_seconds=60),
               dict(name='locust-export-smoke',generator='locust',duration_seconds=60),
               dict(name='k6-export-heavy',generator='k6',duration_seconds=300)]
        report['state']='running'
        for spec in specs:
            spec.update(workload='checkout-heavy',rate=2)
            report['active_window']=spec['name'];r.write(root/'diagnostic-report.json',report)
            scoring_error=None
            try:
                scored=run_window(cell,parent,spec,args.run_id)
            except RuntimeError as exc:
                # A usable file export can coexist with a failed source oracle.
                # Keep this diagnostic about export, retaining the separate score.
                scoring_error=str(exc)
                path=parent/'windows'/spec['name']
                status=json.loads((path/'generator-artifact-status.json').read_text()) if (path/'generator-artifact-status.json').exists() else {}
                if not status.get('verified') or not status.get('semantic_complete'):raise
                score=path/'verification.json'
                scored=json.loads(score.read_text()) if score.exists() else {}
            path=parent/'windows'/spec['name']
            valid=validate_export(path/'generator-artifacts',spec['generator'],'matched',
                                  args.run_id+':'+cell.slug+':'+spec['name'])
            item=dict(name=spec['name'],artifact_verified=valid['verified'],
                      requests=valid['requests'],workload_exit_code=valid['workload_exit_code'],
                      score_state=scored.get('state'),oracle_valid=scored.get('oracle_valid'),
                      source_valid=scored.get('source_valid'),scoring_error=scoring_error)
            report['windows'].append(item)
            r.write(root/'diagnostic-report.json',report)
        report['state']='artifact_export_verified'
    except BaseException as exc:
        report.update(state='incomplete',error=str(exc))
        raise
    finally:
        report['finished_at']=r.utc();report.pop('active_window',None)
        r.write(root/'diagnostic-report.json',report)
        r.checksums(root)
        r._DEADLINE=None
        print('diagnostic evidence:',root,flush=True)


if __name__=='__main__':main()
