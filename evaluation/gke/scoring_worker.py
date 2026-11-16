"""Offline scoring entrypoint; no Kubernetes or cloud credentials are required."""
from __future__ import annotations
import json
import resource
import sys
from pathlib import Path
from types import SimpleNamespace
from .models import read, write
from .evidence_first import MEMORY_BYTES


def score(binding_path):
    if sys.platform!='linux':raise RuntimeError('production isolated scorer requires Linux memory-limit semantics')
    resource.setrlimit(resource.RLIMIT_AS,(MEMORY_BYTES,MEMORY_BYTES))
    binding_path=Path(binding_path);output=binding_path.parent
    binding=read(binding_path)
    if binding['raw_evidence_export'].get('verified') is not True:raise ValueError('unverified raw input')
    path=Path(binding['window'])
    from .evidence_first import verify_inputs
    verify_inputs(path,binding['raw_evidence_export'],projection_proof=binding.get('projected_input_proof'))
    meta=read(path/'scoring-context.json');spec=meta['spec']
    import hashlib
    if meta.get('manifest_sha256') and meta['manifest_sha256']!=hashlib.sha256(json.dumps(binding['manifest'],sort_keys=True,separators=(',',':')).encode()).hexdigest():
        raise ValueError('scoring manifest differs from sealed measurement plan')
    # Evaluate against original read-only inputs. Intermediate evaluator report
    # writes are intentionally deferred to the single compact final writer.
    def capture_write(target,value):
        if Path(target).name!='gke-evaluation.json':raise ValueError('unexpected scoring output target')
    ctx=SimpleNamespace(root=Path(binding['source_root']),manifest=binding['manifest'],write=capture_write)
    if binding.get('projected_input_proof') and ctx.root.resolve()!=Path(__file__).resolve().parents[2]:
        raise ValueError('verified source root differs from executing scorer package')
    if binding.get('verifier_implementation_hashes'):
        from .models import implementation_hashes
        if implementation_hashes(ctx.root)!=binding['verifier_implementation_hashes']:
            raise ValueError('verifier source changed after replay preparation')
    from .evaluate import evaluate
    traffic=read(path/'timing-verification.json') if (path/'timing-verification.json').is_file() else None
    report=evaluate(ctx,spec,path,traffic_report=traffic)
    if report.get('analysis_error'):raise RuntimeError('evaluator failed: '+str(report['analysis_error']))
    report.update(path=str(path),consumer=meta['consumer'],source_selection=meta['source_selection'],timing_error=meta['timing_error'])
    from .models import THESIS_SCHEDULES
    if ctx.manifest.get('schedule') in THESIS_SCHEDULES:
        from .thesis import score_window_answers
        inventory=read(path/'consumer/kubernetes-oracle.json') if (path/'consumer/kubernetes-oracle.json').is_file() else {}
        ledger=read(path/'consumer/ledger.json') if (path/'consumer/ledger.json').is_file() else {}
        report['thesis_answers']=score_window_answers(ledger,inventory.get('before'),inventory.get('after'),report)
    valid=meta['reference_valid'] and meta['consumer_valid'] and meta['source_selection'].get('state')=='pass'
    report['diagnostic_required_evidence']={'valid':valid,'reference':meta['reference_valid'],'consumer':meta['consumer_valid'],'selection':meta['source_selection'].get('state')=='pass'}
    if not valid:
        report['evidence'].update(state='incomplete',valid=False)
        report['reason']='reference, API-only consumer, or deployed source-selection evidence incomplete'
    if meta['execution'].get('state')!='complete':report['execution']['state']=meta['execution']['state']
    if spec['phase'] in ('resource','restart') and meta['diagnostic_state'].get('state')!='complete':report['execution']['state']='incomplete'
    report['analysis']={'state':'complete'};report['raw_evidence_export']=binding['raw_evidence_export']
    report['input_population']=binding.get('input_population','original-local')
    report['projected_input_proof']=binding.get('projected_input_proof')
    report['verifier_implementation_hashes']=binding.get('verifier_implementation_hashes')
    from .compact_report import write_report
    from .report_summary import save_summary
    verify_inputs(path,binding['raw_evidence_export'],projection_proof=binding.get('projected_input_proof'))
    write_report(output/'gke-evaluation.json',report)
    save_summary(output,report)
    write(output/'usage.json',{'max_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'limit_bytes':MEMORY_BYTES,'limit_semantics':'RLIMIT_AS virtual address space, not RSS'})

if __name__=='__main__':score(sys.argv[1])
