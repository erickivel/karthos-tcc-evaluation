"""Offline accuracy reanalysis of one sealed window; never modifies its evidence.

python -m evaluation.gke.accuracy_reanalysis ARCHIVE OUTPUT --sha256 CHECKPOINT_SHA
Only affected comparisons are rebuilt. Missing host read timing, lifecycle cache
versions and equivalent traffic populations remain insufficient. No cluster I/O.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

from . import evaluate as e
from .models import sha,write
from .report_summary import family_summaries
from ..references.kubelet import read_summary

VERSION='accuracy-evidence-v3.5.1'


def _counts(checks):
    counts=Counter(x.get('state','insufficient') for x in checks)
    return {key:counts[key] for key in ('pass','fail','insufficient','incomplete','not-applicable')}


def _state(checks):
    states=[x.get('state','insufficient') for x in checks]
    return 'fail' if 'fail' in states else 'pass' if states and all(x in ('pass','not-applicable') for x in states) and 'pass' in states else 'insufficient'


from .reference_inventory import inventory_declarations,lifecycle_snapshots


def stored_export_counter(points,start,export_end,workload_end,alignment):
    from ..scoring import counter_window
    eligible=[(t,v) for t,v in points if t<=export_end]
    right=max((t for t,_ in eligible),default=None)
    if right is None or right<workload_end:return {'increase':None,'resets':0,'reason':'stored export does not reach workload end'}
    return counter_window(eligible,start,right,alignment=alignment)



def settled_mesh_duration(traffic,verification,names,document,records,thresholds):
    from .population import settled_diagnostics
    from ..scoring import stamp,prometheus,counter_window
    if traffic.get('manifest',{}).get('source_id')!='mesh.istio':return {'state':'not-applicable','reason':'trace populations checked directly'}
    try:
        if traffic.get('eventual_completeness',{}).get('exact') is not True:return {'state':'insufficient','reason':'recorded quiet evidence invalid'}
        before=document('quiet-before.json');after=document('quiet-after.json')
        lo=min(stamp(v) for v in before['records'][-1]['sample_times'].values());hi=max(stamp(v) for v in after['records'][-1]['sample_times'].values())
        namespace=traffic['manifest']['namespace'];target={'boutique':'checkoutservice','astronomy':'checkout'}[traffic['manifest']['demo']]
        native=defaultdict(list);stored=defaultdict(list)
        for name in names:
            if name in ('istio-samples.ndjson','istio-samples.ndjson.gz'):
                for row in records(name):
                    for metric,labels,value in prometheus(row.get('text','')):
                        if labels.get('reporter')=='destination' and labels.get('source_canonical_service')=='frontend' and labels.get('destination_canonical_service')==target and labels.get('source_workload_namespace')==namespace and labels.get('destination_service_namespace')==namespace:
                            native[(metric,json.dumps(labels,sort_keys=True),row.get('pod'))].append((stamp(row['captured_at']),value))
            elif name in ('vm-raw.ndjson','vm-raw.ndjson.gz'):
                for row in records(name):
                    labels=row.get('metric',{})
                    if labels.get('source')=='mesh.istio' and labels.get('edge_client')=='frontend' and labels.get('edge_server')==target and labels.get('client_namespace')==namespace and labels.get('server_namespace')==namespace and labels.get('__name__') in ('servicegraph_request_count','servicegraph_request_duration_sum','servicegraph_request_duration_count'):
                        stored[json.dumps(labels,sort_keys=True)].extend((at/1000,value) for at,value in zip(row.get('timestamps',[]),row.get('values',[])))
        native_series=[dict(metric=metric,labels=json.loads(labels),origin_pod=pod,**counter_window(points,lo,hi,alignment=thresholds.get('max_mesh_counter_boundary_seconds',15))) for (metric,labels,pod),points in native.items()]
        stored_series=[dict(metric=json.loads(labels),**counter_window(points,lo,hi,alignment=thresholds.get('max_vm_counter_boundary_seconds',45))) for labels,points in stored.items()]
        diagnostics=settled_diagnostics(traffic,native_series,stored_series,verification.get('vm',{}).get('series',[]),[lo,hi],thresholds)
        result=dict(diagnostics['quiet_to_quiet'],native_settled_to_frozen_store=diagnostics['native_settled_to_frozen_store'])
        request=document('extended/vm-export-request.json')
        if request.get('end') is not None:
            export_end=stamp(request['end']);load_end=traffic['windows']['whole']['bounds_seconds'][1]
            drain_series=[dict(metric=json.loads(labels),**stored_export_counter(points,lo,export_end,load_end,thresholds.get('max_vm_counter_boundary_seconds',45))) for labels,points in stored.items()]
            from .population import settled_duration
            drain=settled_duration(traffic,native_series,drain_series,[lo,hi],thresholds)
            drain.update(scope='native recorded quiet observations versus last stored samples within the original declared drain export; original fixed window unchanged',declared_export_end=export_end,stored_series=drain_series,
                         endpoint_rule='latest timestamp at/before the original export horizon, independent of values; no search for agreement')
            result['native_settled_to_declared_drain_export']=drain
        result.update(native_series=native_series,stored_series=stored_series,observation_rule='recorded quiet-before/after timestamps only; no search for agreement')
        return result
    except (KeyError,ValueError,TypeError) as exc:return {'state':'insufficient','reason':'settled duration evidence unavailable: '+str(exc)}



def reanalyse_archive(archive,destination,*,expected_sha256,analysis_archive=None,analysis_sha256=None):
    archive=Path(archive).resolve();destination=Path(destination).resolve()
    if destination.exists():raise ValueError('analysis destination must be new')
    if archive in destination.parents or destination in archive.parents:raise ValueError('analysis destination overlaps source')
    if sha(archive)!=expected_sha256:raise ValueError('archive checksum differs from checkpoint')
    used={}
    from .archive_inputs import open_archives
    with open_archives(archive,expected_sha256,analysis_archive=analysis_archive,analysis_sha256=analysis_sha256) as tar:
        members=tar.getmembers();names=[m.name for m in members]
        if len(names)!=len(set(names)) or any(not (m.isfile() or m.isdir()) or m.name.startswith('/') or '..' in Path(m.name).parts for m in members):
            raise ValueError('unsafe or duplicate archive member')
        seal=tar.extractfile('GKE-SHA256SUMS').read().decode()
        digests={}
        for line in seal.splitlines():
            digest,name=line.split('  ',1)
            if name in digests:raise ValueError('duplicate seal member')
            digests[name]=digest
        def payload(name):
            member=tar.getmember(name)
            raw=tar.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest()!=digests.get(name):raise ValueError('member checksum differs: '+name)
            used[name]={'sha256':digests[name],'bytes':member.size}
            return raw
        def document(name,default=None):
            if name=='gke-evaluation.json' and name in names:
                from .analysis_reader import read_analysis_report
                value,size=read_analysis_report(tar.extractfile(name),digests[name],open_member=tar.extractfile,input_members=used)
                used[name]={'sha256':digests[name],'bytes':size,'analysis_projection':'omit duplicated matched-reference payloads and availability bodies; originals retained'}
                return value
            return json.loads(payload(name)) if name in names else ({} if default is None else default)
        def records(name):
            raw=payload(name)
            stream=gzip.GzipFile(fileobj=io.BytesIO(raw)) if name.endswith('.gz') else io.BytesIO(raw)
            with stream:
                for line in stream:
                    if line.strip():yield json.loads(line)
        old=document('gke-evaluation.json');tar.validate_report(old);verification=document('verification.json')
        snapshots=document('extended/kubernetes-inventory.json').get('snapshots',[])
        for name in names:
            if name in ('references/kubernetes-inventory.ndjson','references/kubernetes-inventory.ndjson.gz'):snapshots.extend(records(name))
        node_uids={row['metadata']['name']:row['metadata']['uid'] for row in document('structured/nodes-before.json').get('items',[]) if row.get('metadata',{}).get('uid')}
        inventory_members=[]
        for name in names:
            if name.endswith('.json') and (name in ('k8s-pods.json','karthos-pods.json','generator-pods.json') or name.startswith('pods-before-') or ('inventory-' in name and name.startswith('structured/'))):
                declarations=inventory_declarations(document(name),node_uids,name)
                if declarations['items']:snapshots.append(declarations);inventory_members.append(name)
        diagnostics=[];cadences=[]
        if 'execution-diagnostics/lifecycle.ndjson' in names:diagnostics=list(records('execution-diagnostics/lifecycle.ndjson'))
        if 'execution-diagnostics/cadence.ndjson' in names:cadences=list(records('execution-diagnostics/cadence.ndjson'))
        snapshots.extend(lifecycle_snapshots(diagnostics,cadences,node_uids))
        recovery_inventory={'additional_inventory_members':inventory_members,'lifecycle_records':len(diagnostics),'cadence_records':len(cadences),'reference_snapshots':len(snapshots)}
        original=old.get('numerical_agreement',{}).get('checks',[])
        volume_checks=[c for c in original if c.get('observed',{}).get('metric','').startswith(('k8s.pod.volume.','k8s.volume.'))]
        targets={(c['observed']['pod_uid'],c['observed']['dimensions']['volume']) for c in volume_checks}
        inputs=defaultdict(list);references=defaultdict(list);receiver_pods=0
        for name in names:
            if name.startswith('structured/agent/') and name.endswith(('raw/receiver-input.ndjson','raw/receiver-input.ndjson.gz')):
                for row in records(name):
                    inputs[(row.get('collector_uid'),row.get('node_name'))].append(row)
                    if row.get('path','').split('?')[0]=='/pods' and row.get('status')==200 and not row.get('error') and row.get('collector_uid') and node_uids.get(row.get('node_name')):
                        begin=row.get('requested_at_ns');finish=row.get('received_at_ns')
                        if type(begin) is not int or type(finish) is not int or finish<begin:continue
                        declaration=inventory_declarations(row.get('body',{}),node_uids,name)
                        declaration['items']=[pod for pod in declaration['items'] if pod.get('spec',{}).get('nodeName')==row['node_name']]
                        if declaration['items']:
                            declaration.update(acquisition_started_at=begin/1e9,acquisition_ended_at=finish/1e9,
                                reference_origin='exact receiver /pods; source-input declaration, not independent API history',receiver_collector_uid=row['collector_uid'])
                            snapshots.append(declaration);receiver_pods+=1
            if name.startswith('kubelet-') and name.endswith(('.ndjson','.ndjson.gz')):
                for row in records(name):
                    body=row.get('summary');node=row.get('node_name')
                    if not body or row.get('error'):continue
                    node=node or body.get('node',{}).get('nodeName')
                    requested=row.get('requested_at_ns');received=row.get('acquisition_received_at_ns',row.get('received_at_ns'))
                    if requested is None or received is None:continue
                    for reading in read_summary(body,node=node,requested_at=requested/1e9,received_at=received/1e9):
                        key=(reading.get('pod_uid'),reading.get('dimensions',{}).get('volume'))
                        if reading.get('scope')=='volume' and key in targets:
                            references[(key[0],key[1],reading['metric'])].append(dict(reading,node_uid=row.get('node_uid')))
        recovery_inventory.update(receiver_pod_snapshots=receiver_pods,reference_snapshots=len(snapshots))
        snapshots_by_uid=defaultdict(list)
        for snapshot in snapshots:
            for pod in snapshot.get('items',[]):
                uid=pod.get('metadata',{}).get('uid')
                if uid:snapshots_by_uid[uid].append(dict(snapshot,items=[pod]))
        from .volume_provenance import source_index,attribute
        producers=set(inputs)
        producer_index=source_index([c.get('observed',{}) for c in original],producers)
        forwarded=Counter()
        revised=[];changes=[]
        for index,check in enumerate(original):
            observed=check.get('observed',{});metric=observed.get('metric','')
            if metric.startswith(('k8s.pod.volume.','k8s.volume.')):
                attribution=None
                if observed.get('source')=='kubelet' and (observed.get('collector_uid'),observed.get('node')) not in producers:
                    attribution=attribute(observed,producer_index);forwarded[attribution['state']]+=1
                    if attribution['state']=='matched':observed=attribution['observed']
                field='fs.availableBytes' if metric.endswith('.available') else 'fs.capacityBytes'
                result=e.compare_volume(observed,inputs[(observed.get('collector_uid'),observed.get('node'))],references[(observed.get('pod_uid'),observed.get('dimensions',{}).get('volume'),field)],snapshots=snapshots_by_uid[observed.get('pod_uid')])
                if attribution and attribution['state']!='matched':result=dict(result,state=attribution['state'],reason=attribution['reason'],producer_attribution=attribution)
            elif metric in ('k8s.container.ready','k8s.container.restarts','k8s.container.restart.count','k8s.pod.phase','k8s.pod.status.phase'):
                result=e.compare_lifecycle(observed,snapshots_by_uid[observed.get('pod_uid')])
            else:result=check
            revised.append(result)
            if result is not check:
                # The original observation/index and sealed input hashes permit
                # exact reconstruction; avoid duplicating giant matched records.
                changes.append({'original_check_index':index,'metric':metric,'old_state':check.get('state'),'new_state':result['state'],
                    'old_reason':check.get('reason'),'new_reason':result.get('reason'),
                    'observed':observed,'reference_value':result.get('reference_value'),
                    'receiver_value':result.get('receiver_value'),'measurement_timestamp':result.get('measurement_timestamp'),
                    'volume_type_proof':result.get('volume_type_proof'),
                    'matching_reference_count':result.get('matching_reference_count'),
                    'conversion':result.get('conversion'),
                    **({'bounded_observation':result['bounded_observation']} if 'bounded_observation' in result else {})})
        from .population import resolve_scope
        traffic=resolve_scope(old.get('traffic',{}).get('report',{}),old.get('spec',{}))
        settled=settled_mesh_duration(traffic,verification,names,document,records,old.get('thresholds',{}))
        from .trace_settled import from_evidence as trace_settled
        settled_trace=trace_settled(traffic,verification,names,document,records,old.get('thresholds',{}))
        selected=[]
        for metric in old.get('selected_accuracy',[]):
            row=dict(metric);name=row['name']
            matches=[c for c in revised if c.get('observed',{}).get('metric',c.get('metric'))==name]
            if name.startswith('servicegraph.'):
                matches=[e.compare_traffic(name,traffic,verification)]
            if matches:row.update(state=_state(matches),checks=matches)
            selected.append(row)
        from .observation_contracts import summarize
        view=dict(old,settled_trace_population=settled_trace,settled_duration_diagnostics=settled,selected_accuracy=selected,numerical_agreement=dict(old.get('numerical_agreement',{}),state=_state(revised),checks=revised))
        families=family_summaries(view)
        reasons=Counter((c.get('observed',{}).get('metric',c.get('metric')),c.get('state'),c.get('reason')) for c in revised)
        summary={'analysis_version':VERSION,'spec':old.get('spec'),
            'original_families':old.get('families',{}),'families':families,
            'settled_trace_population':settled_trace,
            'recovery_inventory':dict(recovery_inventory,forwarded_volume_attribution=dict(forwarded)),'settled_duration':settled,
            'bounded_observations':summarize(revised),
            'resources':{'original_counts':_counts(original),'revised_counts':_counts(revised),
                'by_metric_reason':[dict(metric=k[0],state=k[1],reason=k[2],comparisons=n) for k,n in reasons.items()],
                'container_checks':len(old.get('resources',{}).get('checks',[]))},
            'traffic':{row['name']:row['checks'] for row in selected if row['name'].startswith('servicegraph.')},
            'limitations':['unchanged evidence and scoring tolerances; no new measurements',
                'host collector source-read timing and cached lifecycle versions cannot be reconstructed',
                'traffic bracketing agreement is separate from exact call membership',
                'comparison counts are not metric/sample-loss percentages']}
    if sha(archive)!=expected_sha256:raise ValueError('source archive changed during analysis')
    # Publish only after archive/member verification and all calculations finish.
    destination.mkdir(parents=True,exist_ok=False)
    write(destination/'summary.json',summary);write(destination/'comparison-changes.json',changes)
    from .comparison_details import details
    with gzip.GzipFile(filename=str(destination/'resource-comparisons.ndjson.gz'),mode='wb',mtime=0) as output:
        for index,check in enumerate(revised):
            output.write((json.dumps(dict(index=index,**details(check)),sort_keys=True)+'\n').encode())
    write(destination/'analysis-manifest.json',{'analysis_version':VERSION,'archive':str(archive),'archive_sha256':expected_sha256,'analysis_archive':str(analysis_archive) if analysis_archive else None,'analysis_archive_sha256':analysis_sha256,'used_members':used,'member_origins':{name:tar.origins[name] for name in used},
        'implementation_sha256':{name:sha(Path(__file__).parent/name) for name in ('accuracy_reanalysis.py','evaluate.py','report_summary.py','population.py','trace_settled.py','volume_provenance.py','reference_inventory.py','comparison_details.py')},
        'historical_results_modified':False,'reporting_revision_note':'3.5.1 adds resource value/error/tolerance rows; comparison rules identical to 3.5.0'})
    write(destination/'SHA256SUMS',''.join(sha(p)+'  '+p.name+'\n' for p in sorted(destination.iterdir()) if p.is_file()))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('archive',type=Path);parser.add_argument('destination',type=Path);parser.add_argument('--sha256',required=True)
    parser.add_argument('--analysis-archive',type=Path);parser.add_argument('--analysis-sha256')
    args=parser.parse_args();result=reanalyse_archive(args.archive,args.destination,expected_sha256=args.sha256,analysis_archive=args.analysis_archive,analysis_sha256=args.analysis_sha256)
    print(json.dumps({'analysis_version':VERSION,'output':str(args.destination),'families':result['families']}))


if __name__=='__main__':main()
