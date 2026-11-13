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

VERSION='accuracy-evidence-v1.0.1'


def _counts(checks):
    counts=Counter(x.get('state','insufficient') for x in checks)
    return {key:counts[key] for key in ('pass','fail','insufficient','incomplete','not-applicable')}


def _state(checks):
    states=[x.get('state','insufficient') for x in checks]
    return 'fail' if 'fail' in states else 'pass' if states and all(x in ('pass','not-applicable') for x in states) and 'pass' in states else 'insufficient'


def reanalyse_archive(archive,destination,*,expected_sha256):
    archive=Path(archive).resolve();destination=Path(destination).resolve()
    if destination.exists():raise ValueError('analysis destination must be new')
    if archive in destination.parents or destination in archive.parents:raise ValueError('analysis destination overlaps source')
    if sha(archive)!=expected_sha256:raise ValueError('archive checksum differs from checkpoint')
    used={}
    with tarfile.open(archive,'r:gz') as tar:
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
            return json.loads(payload(name)) if name in names else ({} if default is None else default)
        def records(name):
            raw=payload(name)
            stream=gzip.GzipFile(fileobj=io.BytesIO(raw)) if name.endswith('.gz') else io.BytesIO(raw)
            with stream:
                for line in stream:
                    if line.strip():yield json.loads(line)
        old=document('gke-evaluation.json');verification=document('verification.json')
        snapshots=document('extended/kubernetes-inventory.json').get('snapshots',[])
        for name in names:
            if name in ('references/kubernetes-inventory.ndjson','references/kubernetes-inventory.ndjson.gz'):snapshots.extend(records(name))
        original=old.get('numerical_agreement',{}).get('checks',[])
        volume_checks=[c for c in original if c.get('observed',{}).get('metric','').startswith(('k8s.pod.volume.','k8s.volume.'))]
        targets={(c['observed']['pod_uid'],c['observed']['dimensions']['volume']) for c in volume_checks}
        inputs=defaultdict(list);references=defaultdict(list)
        for name in names:
            if name.startswith('structured/agent/') and name.endswith(('raw/receiver-input.ndjson','raw/receiver-input.ndjson.gz')):
                for row in records(name):inputs[(row.get('collector_uid'),row.get('node_name'))].append(row)
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
        revised=[];changes=[]
        for index,check in enumerate(original):
            observed=check.get('observed',{});metric=observed.get('metric','')
            if metric.startswith(('k8s.pod.volume.','k8s.volume.')):
                field='fs.availableBytes' if metric.endswith('.available') else 'fs.capacityBytes'
                result=e.compare_volume(observed,inputs[(observed.get('collector_uid'),observed.get('node'))],references[(observed.get('pod_uid'),observed.get('dimensions',{}).get('volume'),field)],snapshots=snapshots)
            elif metric in ('k8s.container.ready','k8s.container.restarts','k8s.container.restart.count','k8s.pod.phase','k8s.pod.status.phase'):
                result=e.compare_lifecycle(observed,snapshots)
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
                    'conversion':result.get('conversion')})
        traffic=old.get('traffic',{}).get('report',{})
        selected=[]
        for metric in old.get('selected_accuracy',[]):
            row=dict(metric);name=row['name']
            matches=[c for c in revised if c.get('observed',{}).get('metric',c.get('metric'))==name]
            if name.startswith('servicegraph.'):
                matches=[e.compare_traffic(name,traffic,verification)]
            if matches:row.update(state=_state(matches),checks=matches)
            selected.append(row)
        view=dict(old,selected_accuracy=selected,numerical_agreement=dict(old.get('numerical_agreement',{}),state=_state(revised),checks=revised))
        families=family_summaries(view)
        reasons=Counter((c.get('observed',{}).get('metric',c.get('metric')),c.get('state'),c.get('reason')) for c in revised)
        summary={'analysis_version':VERSION,'spec':old.get('spec'),
            'original_families':old.get('families',{}),'families':families,
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
    write(destination/'analysis-manifest.json',{'analysis_version':VERSION,'archive':str(archive),'archive_sha256':expected_sha256,'used_members':used,
        'implementation_sha256':{name:sha(Path(__file__).parent/name) for name in ('accuracy_reanalysis.py','evaluate.py','report_summary.py')},
        'historical_results_modified':False})
    write(destination/'SHA256SUMS',''.join(sha(p)+'  '+p.name+'\n' for p in sorted(destination.iterdir()) if p.is_file()))
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('archive',type=Path);parser.add_argument('destination',type=Path);parser.add_argument('--sha256',required=True)
    args=parser.parse_args();result=reanalyse_archive(args.archive,args.destination,expected_sha256=args.sha256)
    print(json.dumps({'analysis_version':VERSION,'output':str(args.destination),'families':result['families']}))


if __name__=='__main__':main()
