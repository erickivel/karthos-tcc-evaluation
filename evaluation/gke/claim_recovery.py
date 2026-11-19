"""Versioned offline claim recovery. Never represents reconstructed queries as executed."""
import copy,hashlib,json,tarfile
from pathlib import Path
from .analysis_reader import read_analysis_report
from .models import write,sha
from .thesis import _caller_scores,_error_scores
from .thesis_comparison import service_graph_evidence
from ..consumers.api_only import observation_intersects,graph_calls_source


def recover_calls(ledger):
    recovered=copy.deepcopy(ledger);scope=recovered.get('scope',{});source=graph_calls_source(scope.get('metric_source'))
    if scope.get('calls_source')==source:return recovered,False
    graph=recovered.get('after',{});names={n['id']:n for n in graph.get('nodes',[])}
    edges=[e for e in graph.get('edges',[]) if e.get('relation')=='calls' and e.get('target')==scope.get('target_id')
           and e.get('provenance')==[source] and observation_intersects(e,scope.get('from'),scope.get('to'))]
    rows=[]
    for e in edges:
        caller=names.get(e['source'],{})
        if caller.get('kind')=='service' and caller.get('namespace')==scope.get('namespace'):
            rows.append(dict(caller=caller['name'],caller_id=e['source'],target_id=e['target'],namespace=scope['namespace'],provenance=e['provenance'],first_seen=e['first_seen'],last_seen=e['last_seen']))
    scope['calls_source']=source
    recovered['answers']['interval-callers']={'state':'observed' if rows else 'insufficient','value':rows}
    recovered['edge_history']={'state':'observed' if rows else 'insufficient','edges':edges,'requests':[],
        'recovery':'retained snapshot observations overlapping original interval; corrected endpoint query was not executed; historical caller-set completeness is not established'}
    return recovered,True


def recover_archive(archive,expected,destination,accuracy,*,analysis_archive=None,analysis_sha256=None):
    archive=Path(archive);destination=Path(destination)
    if destination.exists():raise ValueError('destination must be new')
    if sha(archive)!=expected:raise ValueError('archive checksum mismatch')
    from .archive_inputs import open_archives
    with open_archives(archive,expected,analysis_archive=analysis_archive,analysis_sha256=analysis_sha256) as tar:
        names=set(tar.getnames());seals={line.split('  ',1)[1]:line.split('  ',1)[0] for line in tar.extractfile('GKE-SHA256SUMS').read().decode().splitlines()}
        used={}
        def payload(name):
            raw=tar.extractfile(name).read()
            if hashlib.sha256(raw).hexdigest()!=seals.get(name):raise ValueError('member checksum mismatch: '+name)
            used[name]=seals[name];return raw
        compact_inputs={}
        old,size=read_analysis_report(tar.extractfile('gke-evaluation.json'),seals['gke-evaluation.json'],open_member=tar.extractfile,input_members=compact_inputs);used['gke-evaluation.json']=seals['gke-evaluation.json']
        tar.validate_report(old)
        used.update({name:item['sha256'] for name,item in compact_inputs.items()})
        ledger=json.loads(payload('consumer/ledger.json'))
        original_caller=_caller_scores(ledger,old)
        recovered,changed=recover_calls(ledger)
        caller=_caller_scores(recovered,old)
        provenance,numeric=_error_scores(ledger,old)
        destination.mkdir(parents=True)
        for name in ('api-nodes.json','api-edges.json','k8s-services.json','k8s-pods.json','k8s-nodes.json','versions-after.json','generator-job-status.json','reference-setup/reference-daemonset.json'):
            if name in names:
                target=destination/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload(name))
        write(destination/'consumer/ledger.json',recovered)
        report={key:old[key] for key in ('spec','manifest','source_selection','evidence','execution','comparison_inputs','coverage','thesis_answers') if key in old}
        # Existing bounded summary carries traffic identities without huge availability payloads.
        summary=json.loads(payload('orchestration-summary.json'))
        report['traffic']=summary['traffic'];report['path']=str(destination)
        report['families']=accuracy['families']
        report['thesis_answers'].update({'interval-callers':caller,'request-error-derivation':provenance,'request-error-numerical-agreement':numeric})
        report['caller_recovery']={'performed':changed,'original_query_rescored':original_caller,'recovered_answer':caller,
            'corrected_endpoint_executed':not changed,'limitation':recovered.get('edge_history',{}).get('recovery')}
        if changed:
            # Positive observations support the declared edge, but cannot prove an
            # unexecuted filtered-history API query or complete caller population.
            report['thesis_answers']['interval-callers']={'state':'insufficient','recovered_known_caller':caller,
                'reason':'corrected filtered-history query was not executed; retained positive observation recovered separately'}
        report['service_graph_evidence']=service_graph_evidence(destination,old['manifest']['namespace'],old['spec']['demo'])
        from .independent_claims import structural_fidelity, single_contribution
        report['structural_fidelity']=structural_fidelity(destination,old['manifest']['namespace'])
        report['settled_trace_population']=accuracy.get('settled_trace_population',{})
        report['single_contribution']=single_contribution(report,old.get('thresholds',{}))
        import tempfile
        from .background_traffic import from_window
        with tempfile.TemporaryDirectory() as temporary:
            observed=Path(temporary);namespace=old['manifest']['namespace']
            for name in names:
                if name=='k8s-pods.json' or name.startswith('logs/'+namespace+'__') and name.endswith('__eval-observer.log'):
                    target=observed/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload(name))
            manifest=old['manifest']
            report['background_traffic']=from_window(observed,namespace,manifest.get('run_id'),manifest.get('start'),manifest.get('end'))

        write(destination/'report.json',report)
        write(destination/'inputs.json',{'archive_sha256':expected,'analysis_archive':str(analysis_archive) if analysis_archive else None,'analysis_archive_sha256':analysis_sha256,'members':used,'member_origins':{name:tar.origins[name] for name in used},'source_revision':old.get('manifest',{}).get('validated_product_revision'),
            'verifier_hashes':{str(p):sha(p) for p in (Path(__file__),Path(__file__).with_name('thesis.py'),Path(__file__).with_name('thesis_comparison.py'),Path(__file__).with_name('independent_claims.py'))},'originals_modified':False})
    return report

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path);parser.add_argument('destination',type=Path)
    parser.add_argument('--sha256',required=True);parser.add_argument('--accuracy',required=True,type=Path)
    parser.add_argument('--analysis-archive',type=Path);parser.add_argument('--analysis-sha256')
    args=parser.parse_args()
    recover_archive(args.archive,args.sha256,args.destination,json.loads(args.accuracy.read_text()),analysis_archive=args.analysis_archive,analysis_sha256=args.analysis_sha256)
