"""Generate the report support data; run as a module with explicit paths."""
import json,hashlib,tarfile,sys
from pathlib import Path
from collections import Counter,defaultdict
from evaluation.gke.models import read,write,sha
from evaluation.gke.baseline_results import baseline_results
from evaluation.gke.independent_claims import no_double_counting


def verify_seal(root, name='SHA256SUMS', required=None):
    root=Path(root).resolve();entries={}
    for line in (root/name).read_text().splitlines():
        digest,relative=line.split('  ',1)
        path=root/relative
        if relative in entries or Path(relative).is_absolute() or '..' in Path(relative).parts or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('unsafe or duplicate sealed member')
        entries[relative]=digest
    for relative in entries if required is None else required:
        path=root/relative
        if relative not in entries or not path.is_file() or sha(path)!=entries[relative]:
            raise ValueError('sealed artifact unavailable or changed: '+relative)
    return entries


def verified_reports(path, window, receipt, claims_subdir='claims'):
    verify_seal(path/'accuracy');verify_seal(path/claims_subdir)
    raw=next(o for o in receipt['objects'] if o['window']==window and o['kind']=='raw')
    analysis=next(o for o in receipt['objects'] if o['window']==window and o['kind']=='analysis')
    for filename in ('accuracy/analysis-manifest.json',claims_subdir+'/inputs.json'):
        inputs=read(path/filename)
        if inputs.get('archive_sha256')!=raw['sha256'] or inputs.get('analysis_archive_sha256')!=analysis['sha256']:
            raise ValueError('derived inputs differ from expected archived window')
    report=read(path/claims_subdir/'report.json');accuracy=read(path/'accuracy/summary.json')
    spec=report.get('spec',{})
    if spec!=accuracy.get('spec') or spec.get('logical_id',spec.get('id'))!=window:
        raise ValueError('derived report/window identity mismatch')
    return report,accuracy


def campaign_metadata(run_id, runtime_commit, role, receipt, readback):
    """Bind explicit campaign labels to offline evidence; never infer runtime identity."""
    if run_id is None and runtime_commit is None and role is None:
        return dict(original_run='full-bounded-study-20261117',runtime_frozen_commit='6e9304e',
                    complement=dict(state='preparing',measurements_started=False))
    import re
    from urllib.parse import urlsplit
    if not run_id or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',run_id) or role!='primary' or not runtime_commit or not re.fullmatch(r'[0-9a-f]{40}',runtime_commit):
        raise ValueError('explicit campaign requires run ID, full runtime commit and primary role')
    def belongs(uri):
        parsed=urlsplit(uri)
        return parsed.scheme=='gs' and bool(parsed.netloc) and ('/runs/'+run_id+'/') in parsed.path and '..' not in parsed.path.split('/')
    # The caller already verifies this digest against the local summary seal.
    seals=[o for o in readback.get('objects',[]) if o.get('uri','').endswith('/final-summary/GKE-SHA256SUMS')]
    if not seals or any(not belongs(o['uri']) or o.get('sha256')!=readback.get('seal_sha256') for o in seals):
        raise ValueError('campaign readback must bind the summary seal to the requested run')
    if readback.get('run_id',run_id)!=run_id:
        raise ValueError('campaign readback run identity differs')
    if not receipt.get('objects'):
        raise ValueError('campaign archives unavailable')
    for item in receipt['objects']:
        if not belongs(item['uri']):raise ValueError('archive campaign differs from requested run')
        path=Path(item['path'])
        if not path.is_file() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:
            raise ValueError('campaign archive unavailable or changed')
    return dict(original_run=run_id,runtime_frozen_commit=runtime_commit,
                campaign=dict(run_id=run_id,role=role,summary_seal_sha256=readback['seal_sha256'],
                              runtime_commit_evidence='caller-supplied; not runtime attestation'))


def main():
    import argparse
    parser=argparse.ArgumentParser(description='Build thesis support outputs from sealed offline reanalysis; no cloud access.')
    parser.add_argument('--analysis-root',type=Path,required=True)
    parser.add_argument('--original-summary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--claims-subdir',default='claims',choices=('claims','claims-final'))
    parser.add_argument('--campaign-run-id',help='Explicit primary campaign ID; requires matching verified archive and readback URIs.')
    parser.add_argument('--runtime-frozen-commit',help='Full runtime commit supplied by the operator; metadata, not runtime attestation.')
    parser.add_argument('--campaign-role',choices=('primary',),help='Use primary for a new full campaign; omit all campaign arguments for legacy reanalysis.')
    args=parser.parse_args()
    base=args.analysis_root;original=args.original_summary
    receipt=read(base/'download-receipt.json')
    readback=read(original/'readback-receipt.json')
    if sha(original/'GKE-SHA256SUMS')!=readback.get('seal_sha256'):raise ValueError('original seal differs from verified readback')
    verify_seal(original,'GKE-SHA256SUMS',required=('matrix.json','attempts.json'))
    matrix=read(original/'matrix.json');attempts=read(original/'attempts.json')
    if sha(original/'matrix.json')!=receipt['source_matrix_sha256']:raise ValueError('matrix checksum mismatch')
    by_id={row['id']:row for row in matrix}
    for item in receipt['objects']:
        export=by_id[item['window']]['export']
        if item['kind']=='analysis':export=export['analysis_export']
        elif item['kind']!='raw':raise ValueError('unexpected archive role')
        expected=export['objects']['window.tar.gz']
        if item['sha256']!=expected['sha256'] or item['bytes']!=expected['bytes'] or item['uri']!=export['uri'].rstrip('/')+'/window.tar.gz':
            raise ValueError('download receipt differs from original sealed export')

    metadata=campaign_metadata(args.campaign_run_id,args.runtime_frozen_commit,args.campaign_role,receipt,readback)

    reports={};details=[];index=[];footprints=[]
    for item in read(base/'index.json'):
     if item['state']!='analysed':continue
     name=item['window'];p=base/name;r,a=verified_reports(p,name,receipt,args.claims_subdir);reports[name]=(str(p/args.claims_subdir/'report.json'),r)
     details.append(dict(window=name,evidence_valid=r['evidence'].get('valid'),original_families=a['original_families'],reanalysed_families=a['families'],structural_fidelity=r['structural_fidelity'],single_contribution=r['single_contribution'],background_traffic=r['background_traffic'],resources=a['resources'],traffic=a['traffic'],settled_trace_population=a['settled_trace_population'],settled_duration=a['settled_duration'],api_questions=r['thesis_answers']))
     for f in sorted(p.rglob('*')):
      if f.is_file():index.append(dict(path=str(f),sha256=sha(f),bytes=f.stat().st_size))
     if r['spec']['phase']!='baseline':continue
     raw=next(o for o in receipt['objects'] if o['window']==name and o['kind']=='raw')
     if sha(raw['path'])!=raw['sha256']:raise ValueError('raw archive changed')
     with tarfile.open(raw['path']) as t:
      seals={n:h for h,n in (line.split('  ',1) for line in t.extractfile('GKE-SHA256SUMS').read().decode().splitlines())}
      def document(n):
       b=t.extractfile(n).read()
       if hashlib.sha256(b).hexdigest()!=seals[n]:raise ValueError('member mismatch '+n)
       return json.loads(b)
      nodes={n['id']:n for n in document('api-nodes.json')['data']['nodes'] if n.get('namespace')=='karthos-system' and n.get('kind')=='container'}
      groups=defaultdict(dict)
      for filename,metric,unit in [('api-cpu.json','cpu_cores','cores'),('api-memory.json','memory_bytes','bytes')]:
       doc=document(filename);seen=set();latest=max(row['timestamp'] for row in doc['data']['samples'])
       for row in doc['data']['samples']:
        if row['timestamp']!=latest:continue
        entity=row['entity_id']
        if entity not in nodes or row['unit']!=unit or row.get('labels',{}).get('source')!='kubelet':continue
        if entity in seen:raise ValueError('multiple series per container in footprint')
        seen.add(entity);groups[entity][metric]=row['value'];groups[entity]['timestamp']=row['timestamp']
      by_name=defaultdict(lambda:dict(containers=0,cpu_cores=0,memory_bytes=0))
      for entity,row in groups.items():
       if not {'cpu_cores','memory_bytes'}<=set(row):continue
       target=by_name[nodes[entity]['name']];target['containers']+=1;target['cpu_cores']+=row['cpu_cores'];target['memory_bytes']+=row['memory_bytes']
      footprints.append(dict(window=name,evidence_valid=r['evidence'].get('valid'),scope='retained API latest samples for matched containers in karthos-system; descriptive snapshot, not a time average or causal overhead',components=dict(by_name),raw_sha256=raw['sha256'],members={n:seals[n] for n in ('api-cpu.json','api-memory.json','api-nodes.json')}))
    claims={}
    for family in sorted({k for _,r in reports.values() for k in r['families']}):
     claims[family]=baseline_results(matrix,matrix,reports,lambda d,k=family:d['families'].get(k,{}).get('state','insufficient'))
    claims['structural_fidelity']=baseline_results(matrix,matrix,reports,lambda d:d['structural_fidelity']['state'])
    claims['single_contribution']=baseline_results(matrix,matrix,reports,lambda d:d['single_contribution']['state'])
    for claim in claims.values():
     for row in claim['baselines']:
      row.pop('families',None)
      row['attempt_ids']=[a.get('attempt_id') for a in attempts if a.get('logical_id')==row['id']]
      row.pop('attempts',None)
    out=args.output;out.mkdir(parents=True,exist_ok=False)
    write(out/'results.json',dict(**metadata,analysis_rules=a['analysis_version'],historical_results_modified=False,baseline_claims=claims,details=details,no_double_counting=no_double_counting(matrix,reports),original_attempts=[{k:r.get(k) for k in ('logical_id','attempt_id','state','execution','evidence_valid')} for r in attempts]))
    write(out/'component-footprint.json',footprints)
    write(out/'evidence-index.json',dict(input_archives=receipt,derived_files=index,original_summary={f.name:sha(f) for f in original.iterdir() if f.is_file()},verification='archive and consumed member hashes checked; original readback receipt separately verified; no originals modified'))
    print('wrote',out)


if __name__ == "__main__":
    main()
