"""Protocol 4 full boundary capture, isolated to an evaluation window.

RepairCapture(cell, window_path, study_deadline_epoch, config=None) rolls out
bounded OTLP JSON file exporters and the exact kubelet receiver input proxy,
then starts inventory/log capture and three independent sampling loops. Stop
exports immutable archives before guarded restoration. Source gaps, restarts,
legacy rotation and unavailable inputs invalidate capture explicitly in status.json.
Instrumentation is intentionally outside measurement/preroll; its resources
and setup/cleanup time are reported separately. No global debug logging.

File exporter contract: https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/exporter/fileexporter/README.md (archived
in results/root-cause-20261020/upstream). JSON lines, no compression, bounded
append-only buffered files without rotation. The owned emptyDir retains its 512Mi sizeLimit, and extraction rejects expanded archives above MAX_BYTES. Legacy rotated archives remain conservatively invalid: merely retaining some segments does not prove completeness. This also keeps timing-tail offsets stable and avoids rotated unbuffered writes.
"""
from __future__ import annotations
import copy
import base64
from contextlib import nullcontext
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from . import runner as r
from .yamlio import load_mapping
from .capture import ContinuousCapture
from .sample_records import BoundedRecords, sample_limits, project_capacity
from .capture_watchdog import OWNER, fingerprint, restore_patch

NS='karthos-system'
MAX_BYTES=512*1024*1024

def now():return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')

def load_config(text):
    return load_mapping(text)

def instrumentation_plan(kind, cm, workload, owner, settings):
    before_cm,before_work=copy.deepcopy(cm),copy.deepcopy(workload)
    cm,workload=copy.deepcopy(cm),copy.deepcopy(workload)
    keys=[k for k in cm['data'] if k.endswith(('.yaml','.yml'))]
    if len(keys)!=1:raise ValueError('expected one collector YAML config')
    key=keys[0];config=load_config(cm['data'][key])
    exporters=config.setdefault('exporters',{})
    if any(k.startswith('file/evaluation-') for k in exporters):raise ValueError('collector already instrumented')
    exporters['file/evaluation-metrics']={'path':'/diag/metrics.json','format':'json','flush_interval':'1s','append':True}
    pipelines=config['service']['pipelines']
    metrics=[p for n,p in pipelines.items() if n.split('/')[0]=='metrics']
    if not metrics:raise ValueError('metrics source unavailable')
    for pipe in metrics:pipe.setdefault('exporters',[]).append('file/evaluation-metrics')
    if kind=='gateway':
        receivers=list(dict.fromkeys(x for n,p in pipelines.items() if n.split('/')[0]=='traces' for x in p['receivers']))
        if not receivers:raise ValueError('trace ingress source unavailable')
        exporters['file/evaluation-traces']={'path':'/diag/traces.json','format':'json','flush_interval':'1s','append':True}
        pipelines['traces/evaluation-ingress']={'receivers':receivers,'processors':[],'exporters':['file/evaluation-traces']}
    template=workload['spec']['template'];spec=template['spec']
    template.setdefault('metadata',{}).setdefault('annotations',{})[OWNER]=owner
    if any(v['name'] in ('evaluation-capture','evaluation-capture-scripts') for v in spec.get('volumes',[])):raise ValueError('existing capture volume')
    collector=next(c for c in spec['containers'] if c['name']==kind)
    if any(c['name'].startswith('diag-') for c in spec['containers']):raise ValueError('existing diagnostic containers')
    mount={'name':'evaluation-capture','mountPath':'/diag'}
    collector.setdefault('volumeMounts',[]).append(copy.deepcopy(mount))
    image=settings['observer_image']
    if '@sha256:' not in image:raise ValueError('reader image must be immutable')
    reader_resources = {'requests': {'cpu':'5m','memory':'8Mi'}, 'limits': {'cpu':'100m','memory':'64Mi'}}
    if kind == 'gateway' and 'gateway_reader_cpu_limit_m' in settings:
        reader_resources['requests']['cpu'] = '100m'
        reader_resources['limits']['cpu'] = f"{settings['gateway_reader_cpu_limit_m']}m"
    spec['containers'].append({'name':'diag-reader','image':image,'command':['sh','-c','sleep 86400'],'resources':reader_resources,'volumeMounts':[copy.deepcopy(mount)]})
    config_volume=None
    if settings.get('capture_finalization_version')==2:
        volumes=[v['name'] for v in spec.get('volumes',[]) if v.get('configMap',{}).get('name')==cm['metadata']['name']]
        if len(volumes)!=1 or not any(v.get('name')==volumes[0] for v in collector.get('volumeMounts',[])):
            raise ValueError('collector ConfigMap mount unavailable or ambiguous')
        config_volume=volumes[0]
        spec['containers'][-1]['volumeMounts'].append({'name':config_volume,'mountPath':'/evaluation-config','readOnly':True})

    spec.setdefault('volumes',[]).append({'name':'evaluation-capture','emptyDir':{'sizeLimit':'512Mi'}})
    spec.setdefault('initContainers',[]).append({'name':'diag-permissions','image':image,'command':['sh','-c','chmod 0777 /diag'],'resources':{'requests':{'cpu':'5m','memory':'8Mi'},'limits':{'cpu':'50m','memory':'32Mi'}},'volumeMounts':[copy.deepcopy(mount)]})
    endpoint=None
    original_receiver=None
    if kind=='agent':
        receivers=[cfg for name,cfg in config['receivers'].items() if name.split('/')[0].replace('_','')=='kubeletstats']
        if len(receivers)!=1:raise ValueError('exact kubelet receiver source unavailable or ambiguous')
        original_receiver=copy.deepcopy(receivers[0])
        endpoint=receivers[0]['endpoint'];receivers[0]['endpoint']='http://127.0.0.1:19050'
        if not receivers[0].get('insecure_skip_verify',False):raise ValueError('proxy requires verified deployed insecure_skip_verify contract')
        proxy_image=settings['astronomy_locust_image']
        if '@sha256:' not in proxy_image:raise ValueError('proxy image must be immutable')
        env=copy.deepcopy(collector.get('env',[]))
        env.extend([{'name':'CAPTURE_UPSTREAM','value':endpoint},{'name':'CAPTURE_OWNER','value':owner},{'name':'CAPTURE_POD','valueFrom':{'fieldRef':{'fieldPath':'metadata.name'}}},{'name':'CAPTURE_UID','valueFrom':{'fieldRef':{'fieldPath':'metadata.uid'}}},{'name':'CAPTURE_NODE','valueFrom':{'fieldRef':{'fieldPath':'spec.nodeName'}}},{'name':'OTEL_TRACES_EXPORTER','value':'none'}])
        spec['containers'].append({'name':'diag-kubelet-proxy','image':proxy_image,'command':['python3','/scripts/receiver_input_proxy.py'],'env':env,'envFrom':copy.deepcopy(collector.get('envFrom',[])),'resources':{'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'250m','memory':'128Mi'}},'volumeMounts':[copy.deepcopy(mount),{'name':'evaluation-capture-scripts','mountPath':'/scripts','readOnly':True}]})
        spec['volumes'].append({'name':'evaluation-capture-scripts','configMap':{'name':'eval-capture-'+owner}})
    cm['data'][key]=json.dumps(config)
    cm['metadata'].setdefault('annotations',{})[OWNER]=owner
    return {'kind':kind,'owner':owner,'key':key,'before_configmap':before_cm,'before_workload':before_work,'after_configmap':cm,'after_workload':workload,'original_receiver_endpoint':endpoint,'original_receiver':original_receiver,'mounted_config_path':('/evaluation-config/'+key) if config_volume else None,'mounted_config_volume':config_volume,'config_sha256':fingerprint(cm['data']),'template_sha256':fingerprint(template)}

def extract_archive(archive, destination, max_bytes=MAX_BYTES):
    destination=Path(destination)
    if destination.exists():raise FileExistsError(destination)
    with tarfile.open(archive) as tar:
        members=tar.getmembers();total=0;names=set()
        for member in members:
            name=Path(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):raise ValueError('unsafe archive member')
            normalized=str(name)
            if normalized in names:raise ValueError('duplicate archive member')
            names.add(normalized);total+=member.size
        if total>max_bytes:raise ValueError('archive exceeds capture disk bound')
        destination.mkdir(parents=True)
        tar.extractall(destination,filter='data')
    return inspect_directory(destination)

def inspect_directory(destination):
    destination=Path(destination)
    files=[];errors=[]
    for file in sorted(destination.rglob('*')):
        if not file.is_file():continue
        data=file.read_bytes();entry={'path':str(file.relative_to(destination)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()};files.append(entry)
        if not data:errors.append('empty:'+entry['path'])
        if file.suffix in ('.json','.ndjson'):
            for index,line in enumerate(data.splitlines()):
                try:json.loads(line)
                except (ValueError,UnicodeError):errors.append('invalid-json:'+entry['path']+':'+str(index+1))
        if file.suffix=='.json' and file.stem not in ('metrics','traces'):errors.append('rotation:'+entry['path'])
    if not files:errors.append('empty-archive')
    return {'files':files,'errors':errors,'total_bytes':sum(f['bytes'] for f in files)}

def parse_time(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

def signal_coverage(path,signal,bounds,slack,expected_trace_ids=None):
    times=[];observed=set();records=0
    for file in Path(path).glob('*.json'):
        with file.open() as handle:
            for line in handle:
                for resource in json.loads(line).get('resource'+signal,[]):
                    for scope in resource.get('scope'+signal,[]):
                        if signal=='Spans':
                            for span in scope.get('spans',[]):
                                records+=1;observed.add(span.get('traceId','').lower())
                                for key in ('startTimeUnixNano','endTimeUnixNano'):
                                    if int(span.get(key,0))>0:times.append(int(span[key])/1e9)
                        else:
                            for metric in scope.get('metrics',[]):
                                for instrument in ('gauge','sum','histogram','exponentialHistogram','summary'):
                                    for point in metric.get(instrument,{}).get('dataPoints',[]):
                                        records+=1
                                        if int(point.get('timeUnixNano',0))>0:times.append(int(point['timeUnixNano'])/1e9)
    errors=[];ordered=sorted(set(times))
    if not records or not ordered:errors.append('source empty or untimed:'+signal)
    if bounds is None:errors.append('measurement bounds unavailable:'+signal)
    elif ordered:
        # Data outside the requested activity interval remains preserved, but
        # cannot conceal an empty interval between old and future records.
        inside=[t for t in ordered if bounds[0]-slack<=t<=bounds[1]+slack]
        if not inside or inside[0]>bounds[0]+slack or inside[-1]<bounds[1]-slack:errors.append('source coverage incomplete:'+signal)
        if signal=='Metrics':
            if len(inside)<2:errors.append('source lacks multiple observations:'+signal)
            if any(b-a>slack*2.5 for a,b in zip(inside,inside[1:])):errors.append('source sampling gap:'+signal)
    if signal=='Spans' and expected_trace_ids is not None and not observed.intersection(expected_trace_ids):errors.append('no ledger-correlated trace captured')
    return {'records':records,'first_timestamp':ordered[0] if ordered else None,'last_timestamp':ordered[-1] if ordered else None,'measurement_bounds':bounds,'boundary_slack_seconds':slack,'max_metric_gap_seconds':slack*2.5 if signal=='Metrics' else None,'expected_trace_ids':len(expected_trace_ids) if expected_trace_ids is not None else None,'correlated_trace_ids':len(observed.intersection(expected_trace_ids)) if expected_trace_ids is not None else None,'errors':errors}

def collection_interval(plan):
    config=load_config(plan['after_configmap']['data'][plan['key']]);intervals=[10.]
    for receiver in config.get('receivers',{}).values():
        text=receiver.get('collection_interval') if isinstance(receiver,dict) else None
        if text:
            match=re.fullmatch(r'([0-9.]+)(ms|s|m|h)',text)
            if not match:raise ValueError('unknown source collection interval:'+text)
            intervals.append(float(match[1])*{'ms':.001,'s':1,'m':60,'h':3600}[match[2]])
    return max(intervals)

def receiver_errors(rows, uid, plan, bounds=None, node_name=None):
    interval=(plan.get('original_receiver') or {}).get('collection_interval','10s')
    match=re.fullmatch(r'([0-9.]+)(ms|s|m|h)',interval)
    if not match:return ['unknown receiver collection interval:'+interval]
    period=float(match[1])*{'ms':.001,'s':1,'m':60,'h':3600}[match[2]]
    errors=[];previous={}
    for row in rows:
        if row.get('collector_uid')!=uid:errors.append('receiver collector UID mismatch')
        if node_name is not None and row.get('node_name')!=node_name:errors.append('receiver node mismatch')
        if row.get('error') or row.get('status')!=200:errors.append('receiver request failed')
        tick=row['requested_at_ns']/1e9;path=row['path']
        if path in previous and tick-previous[path]>period*1.75:errors.append('receiver input gap:'+path)
        if path in previous and tick<previous[path]:errors.append('receiver input timestamp regression')
        if row.get('received_at_ns',0)<row['requested_at_ns']:errors.append('receiver input response timestamp regression')
        previous[path]=tick
    if not rows:errors.append('receiver input empty')
    receiver=plan.get('original_receiver') or {}
    required=['/stats/summary']
    needs_resources=any(cfg.get('enabled') and ('.request_utilization' in name or '.limit_utilization' in name) for name,cfg in receiver.get('metrics',{}).items())
    if receiver.get('extra_metadata_labels') or needs_resources:required.append('/pods')
    for path in required:
        selected=[row for row in rows if row.get('path','').split('?')[0].rstrip('/')==path and row.get('status')==200 and not row.get('error')]
        if not selected:errors.append('required receiver path missing:'+path);continue
        if bounds:
            first=min(row['requested_at_ns']/1e9 for row in selected);last=max(row['received_at_ns']/1e9 for row in selected)
            if first>bounds[0]+period or last<bounds[1]-period:errors.append('receiver coverage incomplete:'+path)
    return sorted(set(errors))

def pod_errors(before, after):
    errors=[]
    if before['metadata']['uid']!=after['metadata']['uid']:errors.append('collector pod UID changed')
    if before.get('spec',{}).get('nodeName')!=after.get('spec',{}).get('nodeName'):errors.append('collector node changed')
    counts={c['name']:c.get('restartCount',0) for c in before.get('status',{}).get('containerStatuses',[])}
    for container in after.get('status',{}).get('containerStatuses',[]):
        if container.get('restartCount',0)!=counts.get(container['name'],0):errors.append('container restarted:'+container['name'])
    if after['metadata'].get('deletionTimestamp'):errors.append('collector terminating')
    return errors

def watchdog_objects(snapshot, settings):
    name=snapshot['name'];ns=snapshot['namespace'];labels={OWNER:snapshot['owner']}
    def meta():return {'name':name,'namespace':ns,'labels':labels}
    image=settings['astronomy_locust_image']
    if '@sha256:' not in image:raise ValueError('watchdog image must be immutable')
    data={'snapshot.json':json.dumps(snapshot),'capture_watchdog.py':Path(__file__).with_name('capture_watchdog.py').read_text(),'receiver_input_proxy.py':Path(__file__).with_name('receiver_input_proxy.py').read_text()}
    if len(json.dumps(data).encode())>900*1024:raise ValueError('watchdog snapshot exceeds safe ConfigMap bound')
    return [
        {'apiVersion':'v1','kind':'ConfigMap','metadata':meta(),'data':data},
        {'apiVersion':'v1','kind':'ServiceAccount','metadata':meta()},
        {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':meta(),'rules':[
            {'apiGroups':[''],'resources':['configmaps'],'resourceNames':['karthos-agent-config','karthos-gateway-config',name],'verbs':['get','patch']},
            {'apiGroups':[''],'resources':['configmaps'],'resourceNames':['karthos-evaluation-owner'],'verbs':['get']},
            {'apiGroups':['apps'],'resources':['daemonsets'],'resourceNames':['karthos-agent'],'verbs':['get','patch']},
            {'apiGroups':['apps'],'resources':['deployments'],'resourceNames':['karthos-gateway'],'verbs':['get','patch']}]},
        {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':meta(),'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':name},'subjects':[{'kind':'ServiceAccount','name':name,'namespace':ns}]},
        {'apiVersion':'batch/v1','kind':'Job','metadata':meta(),'spec':{'backoffLimit':0,'activeDeadlineSeconds':max(60,int(snapshot['cleanup_deadline_epoch']-time.time())+300),'template':{'metadata':{'labels':labels,'annotations':{'sidecar.istio.io/inject':'false'}},'spec':{'serviceAccountName':name,'restartPolicy':'Never','containers':[{'name':'watchdog','image':image,'command':['python3','/scripts/capture_watchdog.py'],'env':[{'name':'OTEL_TRACES_EXPORTER','value':'none'}],'resources':{'requests':{'cpu':'5m','memory':'32Mi'},'limits':{'cpu':'100m','memory':'128Mi'}},'volumeMounts':[{'name':'scripts','mountPath':'/scripts','readOnly':True}]}],'volumes':[{'name':'scripts','configMap':{'name':name}}]}}}}]

def pod_ready(pod):
    return (pod.get('status',{}).get('phase')=='Running' and not pod.get('metadata',{}).get('deletionTimestamp')
            and any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod.get('status',{}).get('conditions',[])))


def ready_nodes(inventory,expected):
    items=inventory.get('items',[])
    names=[p['metadata']['name'] for p in items if not p.get('metadata',{}).get('deletionTimestamp') and any(c.get('type')=='Ready' and c.get('status')=='True' for c in p.get('status',{}).get('conditions',[]))]
    if len(names)!=expected or len(set(names))!=expected or len(items)!=expected:raise ValueError('expected exactly '+str(expected)+' Ready nodes')
    return sorted(names)


def validate_source_coverage(pods,nodes):
    if not pods:raise ValueError('source collectors unavailable')
    groups={}
    for pod in pods:
        if not pod_ready(pod):raise ValueError('source collector not Ready')
        owners=[o for o in pod.get('metadata',{}).get('ownerReferences',[]) if o.get('controller')]
        if len(owners)!=1 or not owners[0].get('uid'):raise ValueError('source collector controller identity unavailable')
        owner=owners[0];groups.setdefault((owner['kind'],owner['uid']),[]).append(pod)
    for (kind,uid),members in groups.items():
        if kind=='DaemonSet':
            assigned=[p.get('spec',{}).get('nodeName') for p in members]
            if sorted(assigned)!=sorted(nodes):raise ValueError('source collector DaemonSet coverage missing or duplicate: '+uid)
        elif kind=='ReplicaSet':
            if len(members)!=1:raise ValueError('source relay must have exactly one pod per ReplicaSet')
        else:raise ValueError('unsupported source collector controller: '+kind)


class FastCapture(ContinuousCapture):
    """Legacy inventory and log interface with independent 1/2/5 second loops."""
    PERIODS={'kubelet':1.,'mesh':2.,'collectors':5.}
    collector_container_names={'agent','gateway'}
    log_capture_options={'max_bytes':64*1024*1024}
    FILES={'kubelet':'kubelet.ndjson.gz','mesh':'istio-samples.ndjson.gz','collectors':'collector-metrics.ndjson.gz'}
    def __init__(self,cell,path):
        super().__init__(cell,path);self.sampling_errors=[];self.sampler_threads=[]
        self.source_collectors=[];self.collector_inventory={}
        self.sample_writers={};self.sample_settings={};self.capacity_projection={}
        self.acquisition_recoveries={'kubelet':0}
        self.sample_status_lock=threading.Lock()
        self.mesh_write_lock=threading.Lock()
    def _collector_target(self,namespace,pod,container):
        if super()._collector_target(namespace,pod,container):return True
        if namespace!=self.cell.namespace:return False
        source={'boutique':('eval-otlp-relay-','collector'),
                'astronomy':('otel-collector-agent-','opentelemetry-collector')}.get(getattr(self.cell,'demo',None))
        return bool(source and pod.startswith(source[0]) and container==source[1])
    def _collector_pod(self,namespace,name):
        key=(namespace,name)
        if key not in self.collector_inventory:
            file=self.path/('pods-before-'+namespace+'.json')
            pods=json.loads(file.read_text()) if file.exists() else {'items':[]}
            for pod in pods['items']:
                if pod['metadata']['name']==name:self.collector_inventory[key]=pod;break
        return self.collector_inventory.get(key,{})
    def _log_snapshot(self, namespaces):
        if self.sample_settings.get('kubelet_transport') != 'private-kubernetes-proxy':
            return None
        from functools import partial
        from .kubelet_transport import log_proxy, log_request
        root = self.path / 'structured'
        root.mkdir(parents=True, exist_ok=True)
        self.log_proxy_lifecycle = {}
        socket_path = self.stack.enter_context(log_proxy(
            namespaces, r.http_timeout(10), root / 'log-proxy.stderr', lifecycle=self.log_proxy_lifecycle))
        return partial(log_request, socket_path, max_bytes=self.log_capture_options['max_bytes'])

    def _mesh_targets(self):
        return [(ns,pod) for ns,pod in self.mesh_pods if 'checkout' in pod or 'frontend' in pod]

    def _stream_specs(self, *, group_mesh=False):
        if self.sample_settings.get('expected_nodes',1)==1:
            return [(stream,None,self.FILES[stream]) for stream in self.PERIODS]
        # Each live target keeps its own cadence. Capacity preflight still sizes
        # the common mesh artifact from one full tick across all targets.
        targets = self._mesh_targets() if not group_mesh and getattr(self.cell,'source','mesh') == 'mesh' else []
        return ([('kubelet',node,'kubelet-'+node+'.ndjson.gz') for node in self.nodes]
                + [('mesh',target,self.FILES['mesh']) for target in targets or [None]]
                + [('collectors',None,self.FILES['collectors'])])
    def _before_sampling(self):
        if self.sample_settings.get('expected_nodes',1)>1:
            inventory=r.kube_json('get','nodes');self.nodes=ready_nodes(inventory,self.sample_settings['expected_nodes'])
            self.node_uids={p['metadata']['name']:p['metadata']['uid'] for p in inventory['items']}
            r.write(self.path/'structured/sampler-nodes-before.json',inventory)
        self.source_collectors=[self._collector_pod(ns,name) for ns,name in self.collector_pods if ns==self.cell.namespace]
        root=self.path/'structured/source-collectors'
        r.write(root/'inventory-before.json',{'items':self.source_collectors})
        if self.sample_settings.get('expected_nodes',1)==1:
            if len(self.source_collectors)!=1:self.sampling_errors.append({'source':'source-collector','error':'expected exactly one source relay collector'})
        else:
            try:validate_source_coverage(self.source_collectors,self.nodes)
            except ValueError as exc:self.sampling_errors.append({'source':'source-collector','error':str(exc)})
        forwarded={(ns,name) for ns,name,_ in self.collector_forwards}
        for ns,name in self.collector_pods:
            self._collector_pod(ns,name)
            if (ns,name) not in forwarded:self.sampling_errors.append({'source':'collector-selfmetrics','pod':name,'error':'collector selfmetrics unavailable'})
        configs=set()
        for pod in self.source_collectors:
            namespace=pod['metadata']['namespace']
            for volume in pod.get('spec',{}).get('volumes',[]):
                if volume.get('configMap',{}).get('name'):configs.add((namespace,volume['configMap']['name']))
            for container in pod.get('spec',{}).get('containers',[]):
                for env in container.get('envFrom',[]):
                    if env.get('configMapRef',{}).get('name'):configs.add((namespace,env['configMapRef']['name']))
        if not configs:self.sampling_errors.append({'source':'source-config','error':'source collector config references unavailable'})
        for namespace,name in sorted(configs):
            try:r.write(root/'configs'/(namespace+'__'+name+'.json'),r.kube_json('get','configmap',name,'-n',namespace))
            except Exception as exc:self.sampling_errors.append({'source':'source-config','configmap':name,'error':str(exc)})
        if not self.sampling_errors:
            targets=[target for target in self.collector_forwards if target[0]==self.cell.namespace]
            self._sample_once('collectors',root/'startup-selfmetrics.ndjson',targets=targets)
        r.write(root/'startup-status.json',{'ready':not self.sampling_errors,'errors':self.sampling_errors})
        if self.sampling_errors:raise RuntimeError('source collector startup evidence unavailable: '+str(self.sampling_errors))
        self.kubelet_socket=None
        if self.sample_settings.get('kubelet_transport')=='private-kubernetes-proxy':
            from .kubelet_transport import kubelet_proxy
            self.kubelet_proxy_lifecycle = {}
            self.kubelet_socket=self.stack.enter_context(kubelet_proxy(
                self.nodes,r.http_timeout(10),self.path/'structured/kubelet-proxy.stderr',
                lifecycle=self.kubelet_proxy_lifecycle))
        self._sampling_preflight()
    def _read_kubelet_json(self,row,node=None):
        """Bounded read-only recovery; only a valid response becomes a sample.

        JSON/schema validation is not an endpoint-to-endpoint checksum. Retain
        every failed body, and use the successful request's acquisition times.
        """
        node=node or self.node
        row.update(node_name=node,node_uid=getattr(self,'node_uids',{}).get(node),acquisition_policy='bounded-kubelet-json2',acquisition_attempts=[])
        deadline=time.monotonic()+self.PERIODS['kubelet'];last_error=None
        for number in range(1,4):
            remaining=deadline-time.monotonic()
            if remaining<=0 or self.stop_event.is_set():
                reason='kubelet acquisition stopped' if self.stop_event.is_set() else 'kubelet acquisition cadence budget exhausted'
                if last_error is not None:reason += '; last attempt: '+str(last_error)
                last_error=TimeoutError(reason);break
            requested=time.time_ns();response=None
            try:
                timeout=5. if number==1 else min(5.,remaining)
                socket_path=getattr(self,'kubelet_socket',None)
                if socket_path is not None:
                    from .kubelet_transport import kubelet_request
                    row['transport']='private-kubernetes-proxy'
                    response=kubelet_request(socket_path,node,r.http_timeout(timeout))
                else:
                    row['transport']='kubectl-get-raw'
                    response=r.kube('get','--raw',f'/api/v1/nodes/{node}/proxy/stats/summary',
                                    check=False,timeout=timeout,quiet=True,binary=True)
                received=time.time_ns()
                if response.returncode:raise RuntimeError('kubelet request failed with status '+str(response.returncode))
                if len(response.stdout)>32*1024*1024:raise ValueError('kubelet response exceeds byte bound')
                summary=json.loads(response.stdout.decode('utf-8'))
                if not isinstance(summary,dict) or not isinstance(summary.get('node'),dict) or not isinstance(summary.get('pods'),list):
                    raise ValueError('invalid kubelet summary envelope')
                if summary['node'].get('nodeName')!=node:
                    raise ValueError('kubelet summary source node mismatch')
                row['acquisition_attempts'].append(dict(attempt=number,requested_at_ns=requested,received_at_ns=received,
                    returncode=response.returncode,stdout_sha256=hashlib.sha256(response.stdout).hexdigest(),
                    stderr_base64=base64.b64encode(response.stderr).decode('ascii'),error=None,
                    transport_diagnostic=getattr(response,'transport_diagnostic',None)))
                row.update(summary=summary,raw_response_retention='recovery-only',
                    acquisition_received_at_ns=received,acquisition_recovered=number>1)
                if number>1:
                    row['raw_response_base64']=base64.b64encode(response.stdout).decode('ascii')
                    self.acquisition_recoveries['kubelet']+=1
                return requested
            except Exception as exc:
                received=time.time_ns();last_error=exc
                row['acquisition_attempts'].append(dict(attempt=number,requested_at_ns=requested,received_at_ns=received,
                    returncode=response.returncode if response is not None else None,error=str(exc),
                    transport_diagnostic=getattr(exc,'transport_diagnostic',getattr(response,'transport_diagnostic',None)),
                    stdout_base64=base64.b64encode(response.stdout if response is not None else getattr(exc,'output',b'') or b'').decode('ascii'),
                    stderr_base64=base64.b64encode(response.stderr if response is not None else getattr(exc,'stderr',b'') or b'').decode('ascii')))
        raise ValueError('no valid kubelet response within acquisition budget: '+str(last_error))

    def _sample_once(self,stream,path,targets=None):
        if stream=='mesh' and getattr(self.cell,'source','mesh')!='mesh':
            self._record(path,{'source':'mesh','not_applicable':True,'reason':'selected source is '+self.cell.source,'error':None},time.time_ns());return
        if targets is None:
            if stream=='kubelet':targets=[('node',self.node)]
            elif stream=='mesh':targets=self._mesh_targets()
            else:targets=self.collector_forwards
        if not targets:
            self._record(path,{'error':'source unavailable','source':stream},time.time_ns());return
        for target in targets:
            requested=time.time_ns();row={'source':stream}
            try:
                if stream=='kubelet':
                    requested=self._read_kubelet_json(row,target[1])
                elif stream=='mesh':
                    ns,pod=target;row.update(namespace=ns,pod=pod)
                    result=r.kube('get','--raw',f'/api/v1/namespaces/{ns}/pods/{pod}:15090/proxy/stats/prometheus',check=False,timeout=5,quiet=True)
                    if result.returncode:raise RuntimeError(result.stderr)
                    row['text']=result.stdout
                    if not row['text']:raise ValueError('empty mesh source')
                else:
                    ns,pod,base=target;inventory=self._collector_pod(ns,pod)
                    row.update(namespace=ns,pod=pod,collector_uid=inventory.get('metadata',{}).get('uid'),node_name=inventory.get('spec',{}).get('nodeName'),component='source' if ns==self.cell.namespace else ('agent' if pod.startswith('karthos-agent-') else 'gateway'))
                    with r.urllib.request.urlopen(base+'/metrics',timeout=3) as response:row['text']=response.read().decode()
                    if not row['text']:raise ValueError('empty collector source')
                row['error']=None
            except Exception as exc:row['error']=str(exc)
            self._record(path,row,requested)
    def _record(self,path,row,requested):
        received=time.time_ns();row.update(requested_at_ns=requested,received_at_ns=received,requested_at=datetime.fromtimestamp(requested/1e9,timezone.utc).isoformat(),received_at=datetime.fromtimestamp(received/1e9,timezone.utc).isoformat(),captured_at=datetime.fromtimestamp(received/1e9,timezone.utc).isoformat())
        key=str(path)
        with self.mesh_write_lock if row.get('source') == 'mesh' else nullcontext():
            if key not in self.sample_writers:
                self.sample_writers[key]=BoundedRecords(path,*sample_limits(self.sample_settings))
            self.sample_writers[key].append(row)
        if row.get('error'):
            self.sampling_errors.append({'source':row.get('source'),'node_name':row.get('node_name'),
                                         'pod':row.get('pod'),'at_ns':received,'error':row['error']})
    def _sampling_status(self):
        # Separate threads write different streams; publish a single atomic
        # status snapshot so a failed sampler is visible before window export.
        with self.sample_status_lock:
            status={'version':2,'updated_at':now(),'valid':not self.sampling_errors,
                    'acquisition_policy':'bounded-kubelet-json2','acquisition_recoveries':dict(self.acquisition_recoveries),
                    'errors':list(self.sampling_errors),'projections':dict(self.capacity_projection),
                    'streams':{k:v.status() for k,v in list(self.sample_writers.items())}}
            path=self.path/'structured/sampling-status.json';path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix('.tmp');r.write(temporary,status);temporary.replace(path)
        return status
    def _sampling_preflight(self):
        disk,logical=sample_limits(self.sample_settings)
        seconds=self.sample_settings.get('capture_expected_seconds',900)
        if not isinstance(seconds,(int,float)) or isinstance(seconds,bool) or seconds<=0:
            raise ValueError('sampling expected duration must be positive')
        for stream,node,filename in self._stream_specs(group_mesh=True):
            period=self.PERIODS[stream];key=stream+(':'+node if node else '')
            path=self.path/'structured'/('startup-'+filename)
            if node:self._sample_once(stream,path,targets=[('node',node)])
            else:self._sample_once(stream,path)
            writer=self.sample_writers.get(str(path))
            if writer:
                self.capacity_projection[key]=project_capacity(writer.status(),period,seconds,disk,logical)
                if not self.capacity_projection[key]['sufficient']:
                    self.sampling_errors.append({'source':stream,'error':'projected sampling capacity exceeds budget','at':now()})
        projected=sum(item['projected_stored_bytes'] for item in self.capacity_projection.values())
        free=shutil.disk_usage(self.path).free
        reserve=512*1024*1024
        self.capacity_projection['disk']={'free_bytes':free,'sampling_projected_bytes':projected,
            'reserve_bytes':reserve,'scope':'independent sampler streams only; other capture/export budgets remain separate',
            'sufficient':free>=projected+reserve}
        if not self.capacity_projection['disk']['sufficient']:
            self.sampling_errors.append({'source':'sampling-disk','error':'insufficient free disk for projected sampling and reserve','at':now()})
        self._sampling_status()
        if self.sampling_errors:
            raise RuntimeError('independent sampling preflight failed: '+str(self.sampling_errors))
    def _loop(self,stream,target=None,filename=None):
        period=self.PERIODS[stream];due=time.monotonic();previous=None
        previous_sample_seconds=previous_status_seconds=previous_wait_seconds=0.0
        node = target if stream == 'kubelet' else None
        mesh_pod = target[1] if stream == 'mesh' and target else None
        targets = [('node',node)] if node else ([target] if mesh_pod else None)
        while not self.stop_event.is_set():
            tick=time.monotonic()
            if previous is not None and tick-previous>period*2.5:
                self.sampling_errors.append({'source':stream,'node_name':node,'pod':mesh_pod,
                                             'at':now(),'error':'sampling gap','seconds':tick-previous,
                                             'previous_sample_seconds':previous_sample_seconds,
                                             'previous_status_seconds':previous_status_seconds,
                                             'previous_wait_seconds':previous_wait_seconds})
            try:
                started=time.monotonic()
                self._sample_once(stream,self.path/(filename or self.FILES[stream]),targets=targets)
                sampled=time.monotonic();previous_sample_seconds=sampled-started
                self._sampling_status()
                previous_status_seconds=time.monotonic()-sampled
            except Exception as exc:
                self.sampling_errors.append({'source':stream,'error':'sampler terminated:'+str(exc),'terminated_at':now()})
                try:self._sampling_status()
                except Exception as status_error:self.sampling_errors.append({'source':stream,'error':'sampling status write failed:'+str(status_error),'terminated_at':now()})
                return
            previous=tick;due+=period
            if due<time.monotonic():due=time.monotonic()
            wait_started=time.monotonic()
            self.stop_event.wait(max(0,due-wait_started))
            previous_wait_seconds=time.monotonic()-wait_started
    def _sample(self):
        self.sampler_threads=[threading.Thread(target=self._loop,args=(stream,node,filename),daemon=True) for stream,node,filename in self._stream_specs()]
        for thread in self.sampler_threads:thread.start()
        for thread in self.sampler_threads:thread.join()

class CapturePodsPending(ValueError):
    """The exact instrumented pod set has not become Ready yet."""

class RepairCapture:
    def __init__(self,cell,path,study_deadline_epoch,config=None):
        self.cell=cell;self.path=Path(path);self.root=self.path/'structured';self.settings=dict(r.CONFIG,**(config or {}))
        self.gateway_restart_token = self.settings.get('gateway_restart_token')
        if self.gateway_restart_token is not None and (
                not isinstance(self.gateway_restart_token, str)
                or not re.fullmatch(r'w-[0-9a-f]{18}', self.gateway_restart_token)):
            raise ValueError('invalid planned Gateway restart window token')
        expected=self.settings.get('expected_nodes',1)
        if type(expected) is not int or expected<1:raise ValueError('expected_nodes must be a positive integer')
        maximum=self.settings.get('capture_log_max_bytes',64*1024*1024)
        if type(maximum) is not int or not 0<maximum<=512*1024*1024:raise ValueError('capture log budget must be a positive integer <=512MiB')
        sample_disk,sample_logical=sample_limits(self.settings)
        self.deadline=study_deadline_epoch;self.owner=uuid.uuid4().hex[:16];self.name='eval-capture-'+self.owner
        self.started_at=None;self.stopped=False;self.plans=[];self.pods={};self.capture=None;self.watchdog_installed=False
        self.state={'protocol_version':4,'owner':self.owner,'capture_bounds':{'log_bytes_per_stream':maximum,'sampling_disk_bytes_per_stream':sample_disk,'sampling_uncompressed_bytes_per_stream':sample_logical,'sampling_encoding':'gzip-members'},'valid':False,'errors':[],'components':{},'instrumentation':{'setup_started_at':now(),'overhead_resources_reported_separately':True}}
    def status(self):return copy.deepcopy(self.state)
    def _resource(self,kind):return ('daemonset/' if kind=='agent' else 'deployment/')+'karthos-'+kind
    def _cleanup_kube(self,*args,**kwargs):
        # Restoration remains usable after runner's study deadline expires.
        return subprocess.run(['kubectl',*args],text=True,capture_output=True,timeout=kwargs.get('timeout',60),check=True)
    def _get(self,*args,cleanup=False,timeout=None):
        if cleanup:
            return json.loads(self._cleanup_kube(*args,'-o','json',timeout=timeout or 60).stdout)
        if timeout is not None:
            return json.loads(r.kube(*args,'-o','json',timeout=timeout).stdout)
        return r.kube_json(*args)
    def _installation_patch(self,plan,target):
        before=plan['before_'+target];after=plan['after_'+target]
        ops=[{'op':'test','path':'/metadata/uid','value':before['metadata']['uid']}]
        if before['metadata'].get('resourceVersion'):ops.append({'op':'test','path':'/metadata/resourceVersion','value':before['metadata']['resourceVersion']})
        if target=='configmap':
            ops.extend([{'op':'test','path':'/data','value':before['data']},{'op':'replace','path':'/data','value':after['data']},{'op':'add','path':'/metadata/annotations','value':after['metadata']['annotations']}]);resource='configmap/'+before['metadata']['name']
        else:
            ops.extend([{'op':'test','path':'/spec/template','value':before['spec']['template']},{'op':'replace','path':'/spec/template','value':after['spec']['template']}]);resource=self._resource(plan['kind'])
        return resource,ops
    def _wait_watchdog_ready(self):
        # kubectl wait with a selector fails immediately when the Job controller
        # has not created a Pod. Share one bounded budget across both phases.
        deadline=time.monotonic()+min(120.,max(0.,self.deadline-time.time()))
        while True:
            remaining=min(deadline-time.monotonic(),self.deadline-time.time())
            if remaining<=0:raise TimeoutError('watchdog Pod creation/readiness deadline reached')
            response=r.kube('get','pods','-n',NS,'-l',f'job-name={self.name}','-o','json',quiet=True,timeout=min(10.,remaining))
            pods=[pod for pod in json.loads(response.stdout).get('items',[]) if not pod.get('metadata',{}).get('deletionTimestamp')]
            if pods:
                if len(pods)!=1:raise ValueError('watchdog needs exactly one active Pod')
                pod=pods[0];meta=pod['metadata']
                if meta.get('labels',{}).get(OWNER)!=self.owner:raise ValueError('watchdog Pod ownership changed')
                if pod.get('status',{}).get('phase') in ('Failed','Succeeded'):raise RuntimeError('watchdog terminated before readiness')
                remaining=min(deadline-time.monotonic(),self.deadline-time.time())
                if remaining<=0:raise TimeoutError('watchdog Pod readiness deadline reached')
                r.kube('wait','--for=condition=Ready','pod/'+meta['name'],'-n',NS,f'--timeout={remaining:.3f}s',timeout=remaining)
                self.state['instrumentation']['watchdog_ready']={'at':now(),'pod':meta['name'],'uid':meta.get('uid')}
                return
            time.sleep(min(1.,max(0.,min(deadline-time.monotonic(),self.deadline-time.time()))))
    def _prepare(self):
        if time.time()>=self.deadline:raise TimeoutError('study deadline reached before capture setup')
        if self.settings.get('expected_nodes',1)>1:
            inventory=self._get('get','nodes');self.nodes=ready_nodes(inventory,self.settings['expected_nodes'])
            self.node_uids={p['metadata']['name']:p['metadata']['uid'] for p in inventory['items']}
            r.write(self.root/'nodes-before.json',inventory)
        marker=self._get('get','configmap','karthos-evaluation-owner','-n',NS)
        for kind in ('agent','gateway'):
            cm=self._get('get','configmap','karthos-'+kind+'-config','-n',NS);work=self._get('get',self._resource(kind),'-n',NS)
            plan=instrumentation_plan(kind,cm,work,self.owner,self.settings)
            for target in ('configmap','workload'):
                resource,ops=self._installation_patch(plan,target)
                response=r.kube('patch',resource,'-n',NS,'--type=json','-p',json.dumps(ops),'--dry-run=server','-o','json')
                admitted=json.loads(response.stdout)
                if admitted['metadata']['uid']!=plan['before_'+target]['metadata']['uid']:raise ValueError('dry-run resource UID changed')
                if target=='configmap' and admitted['data']!=plan['after_configmap']['data']:raise ValueError('admission changed collector config data')
                plan['after_'+target]=admitted
            plan['config_sha256']=fingerprint(plan['after_configmap']['data'])
            plan['template_sha256']=fingerprint(plan['after_workload']['spec']['template'])
            if kind == 'gateway' and self.gateway_restart_token is not None:
                plan['gateway_restart_token'] = self.gateway_restart_token
            self.plans.append(plan)
            r.write(self.root/kind/'snapshot.json',plan)
        self.snapshot={'name':self.name,'owner':self.owner,'namespace':NS,'ownership_marker_uid':marker['metadata']['uid'],'cleanup_deadline_epoch':self.deadline+int(self.settings.get('artifact_recovery_seconds',1800)),'plans':self.plans}
        r.write(self.root/'watchdog-snapshot.json',self.snapshot)
        # Fully validated snapshots are durable before the first cluster mutation.
        objects=watchdog_objects(self.snapshot,self.settings)
        self.watchdog_installed=True # Partial installation must also be cleaned up.
        r.kube('create','-f','-',input_text=json.dumps({'apiVersion':'v1','kind':'List','items':objects}))
        self._wait_watchdog_ready()
        for plan in self.plans:
            for target in ('configmap','workload'):
                resource,ops=self._installation_patch(plan,target)
                r.kube('patch',resource,'-n',NS,'--type=json','-p',json.dumps(ops))
                current=self._get('get',resource,'-n',NS)
                # Unexpected admission changes fail closed against the durable pre-mutation plan.
                restore_patch(plan,target,current)
            kind = plan['kind']
            rollout_budget = min(240., max(0., self.deadline - time.time()))
            if rollout_budget <= 0:
                raise TimeoutError('capture rollout deadline reached')
            readiness_deadline = time.monotonic() + rollout_budget
            r.kube('rollout','status',self._resource(kind),'-n',NS,f'--timeout={rollout_budget:.3f}s',timeout=rollout_budget)
            selected = self._wait_capture_pods(kind, readiness_deadline) if self.settings.get('expected_nodes',1)>1 else {kind:self._pod(kind)}
            self.pods.update(selected)
            for key,pod in selected.items():r.write(self._destination(key)/'pod.json',pod)
            r.write(self.root/plan['kind']/'inventory-before.json',self._get('get','pods','-n',NS))
        if self.settings.get('capture_finalization_version')==2:self._verify_mounted_configs()
        self.state['instrumentation']['setup_finished_at']=now()
        self.state['instrumentation']['resources']={p['kind']:[{'name':c['name'],'resources':c.get('resources',{})} for c in p['after_workload']['spec']['template']['spec']['containers'] if c['name'].startswith('diag-')] for p in self.plans}
    def _verify_mounted_configs(self):
        """Read the actual same-pod projected volume before any traffic starts."""
        from .gke.setup import validate_connector_settings
        receipts=[]
        for key,pod in self.pods.items():
            kind=key.split(':',1)[0]
            plan=next(p for p in self.plans if p['kind']==kind)
            mounted=plan.get('mounted_config_path')
            if not mounted:raise ValueError('same-pod mounted config path missing')
            response=r.kube('exec','-n',NS,pod['metadata']['name'],'-c','diag-reader','--','cat',mounted,
                            timeout=min(10.,max(.01,self.deadline-time.time())),quiet=True)
            expected=plan['after_configmap']['data'][plan['key']]
            if response.stdout!=expected:raise ValueError('actual mounted collector config differs from generated config')
            current=self._pod(kind,cleanup=True) if ':' not in key else self._pods(kind,cleanup=True)[key]
            if pod_errors(pod,current):raise ValueError('collector changed during mounted config proof')
            receipt={'component':key,'collector_uid':pod['metadata']['uid'],'node_name':pod['spec'].get('nodeName'),
                     'path':mounted,'volume':plan['mounted_config_volume'],
                     'mounted_sha256':hashlib.sha256(response.stdout.encode()).hexdigest(),
                     'same_pod_volume':True,'observed_at':now()}
            if kind=='gateway' and self.cell.source=='trace':
                expected_capacity=self.settings.get('servicegraph_store_max_items')
                if expected_capacity is None:raise ValueError('active servicegraph capacity setting missing')
                receipt['servicegraph']=validate_connector_settings(load_config(response.stdout),expected=expected_capacity)
            receipts.append(receipt)
        self.state['instrumentation']['mounted_config_proof']={'valid':True,'receipts':receipts}
        r.write(self.root/'mounted-config-proof.json',self.state['instrumentation']['mounted_config_proof'])

    def _destination(self,key):
        return self.root.joinpath(*key.split(':',1))
    def _wait_capture_pods(self, kind, deadline):
        # Rollout status can briefly report an earlier ready ReplicaSet while the
        # exact capture-owned replacement is still being admitted or started.
        state = self.state['instrumentation'].setdefault('pod_readiness', {}).setdefault(kind, {'attempts': 0})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'{kind} owned capture pods not Ready within rollout budget: {state.get("last_pending", "no observation")}')
            state['attempts'] += 1
            try:
                pods = self._pods(kind, timeout=min(10., remaining))
            except CapturePodsPending as pending:
                state['last_pending'] = str(pending)
                time.sleep(min(1., max(0., deadline - time.monotonic())))
            else:
                state['ready_at'] = now()
                return pods

    def _capture_candidates(self, kind, cleanup, timeout):
        items = self._get('get', 'pods', '-n', NS, cleanup=cleanup, timeout=timeout)['items']
        return [pod for pod in items if pod['metadata']['name'].startswith('karthos-'+kind+'-')
                and not pod['metadata'].get('deletionTimestamp')
                and pod['metadata'].get('annotations',{}).get(OWNER)==self.owner]

    def _pods(self,kind,cleanup=False,timeout=None):
        if kind=='gateway':
            pod=self._pod('gateway',cleanup=cleanup,timeout=timeout)
            if not pod_ready(pod):raise CapturePodsPending('gateway must be Ready')
            return {'gateway':pod}
        selected = self._capture_candidates(kind, cleanup, timeout)
        grouped={node:[] for node in self.nodes}
        for pod in selected:
            node=pod.get('spec',{}).get('nodeName')
            if node is None and pod.get('status',{}).get('phase') == 'Pending':
                continue
            if node not in grouped:raise ValueError('agent on unexpected node: '+str(node))
            grouped[node].append(pod)
        for node,pods in grouped.items():
            if len(pods) > 1:raise ValueError('agent has ambiguous owned capture pods on '+node)
            if not pods or not pod_ready(pods[0]):raise CapturePodsPending('agent needs exactly one owned Ready running pod on '+node)
        return {'agent:'+node:pods[0] for node,pods in grouped.items()}
    def _pod(self,kind,cleanup=False,timeout=None):
        candidates = [pod for pod in self._capture_candidates(kind, cleanup, timeout)
                      if pod.get('status',{}).get('phase') == 'Running']
        if not candidates:raise CapturePodsPending(f'{kind} capture needs exactly one owned running pod; found 0')
        if len(candidates) > 1:raise ValueError(f'{kind} capture needs exactly one owned running pod; found {len(candidates)}')
        return candidates[0]
    def start(self):
        # Reserve before entering failure cleanup: existing evidence is immutable.
        self.root.mkdir(parents=True,exist_ok=False)
        try:
            self._prepare();self.capture=FastCapture(self.cell,self.path);self.capture.log_capture_options={'max_bytes':self.settings.get('capture_log_max_bytes',64*1024*1024)};self.capture.sample_settings=self.settings;self.capture.start();self.started_at=self.capture.started_at
            self.state['started_at']=self.started_at;r.write(self.root/'status.json',self.state)
            return self
        except BaseException as exc:
            self.state['errors'].append('setup:'+str(exc));self.stop();raise
    def _window_bounds(self):
        manifest=self.path/'manifest.json'
        if not manifest.exists():return None
        data=json.loads(manifest.read_text())
        if not data.get('start') or not data.get('end'):return None
        bounds=(parse_time(data['start']),parse_time(data['end']))
        if bounds[1]<=bounds[0]:raise ValueError('invalid measurement bounds')
        return bounds
    def _trace_activity(self,bounds):
        ledger=self.path/'ledger.ndjson'
        if not ledger.exists():return bounds,set()
        rows=[json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        starts=[parse_time(row['started_at']) for row in rows if row.get('started_at')]
        ends=[parse_time(row['completed_at']) for row in rows if row.get('completed_at')]
        # Native process bounds include report generation; trace coverage
        # follows actual ledger request activity instead.
        activity=(min(starts),max(ends)) if starts and ends else bounds
        return activity,{row['trace_id'].lower() for row in rows if row.get('trace_id')}
    def freeze_snapshots(self):
        """Freeze all owned collector streams once before restoration.

        Pollers must have stopped first. The shared deadline is never renewed by
        a retry, and the source emptyDir never receives a second full copy.
        """
        if hasattr(self,'frozen_snapshots'):
            return self.frozen_snapshots
        from concurrent.futures import ThreadPoolExecutor
        from .capture_finalization import freeze_prefix, SnapshotReader, SnapshotIncomplete, inventory_script
        deadline=time.monotonic()+min(30.,max(0.,self.deadline-time.time()))
        def freeze_one(item):
            key,before=item;kind=key.split(':',1)[0]
            destination=self._destination(key)/'raw-attempt-1';destination.mkdir(parents=True,exist_ok=False)
            result={'collector_uid':before['metadata']['uid'],'node_name':before['spec'].get('nodeName'),
                    'raw_directory':str(destination),'files':{},'errors':[]}
            required={'metrics.json',*({'traces.json'} if kind=='gateway' else {'receiver-input.ndjson'})}
            allowed={'metrics.json','traces.json','receiver-input.ndjson','receiver-errors.ndjson'}
            try:
                observed=self._pod(kind,cleanup=True,timeout=min(5.,max(.01,deadline-time.monotonic()))) if ':' not in key else self._pods(kind,cleanup=True,timeout=min(5.,max(.01,deadline-time.monotonic())))[key]
                errors=pod_errors(before,observed)
                if errors:raise ValueError('; '.join(errors))
                response=r.kube('exec','-n',NS,before['metadata']['name'],'-c','diag-reader','--',
                    'sh','-c',inventory_script(),timeout=min(5.,max(.01,deadline-time.monotonic())),quiet=True)
                listed=set()
                for line in response.stdout.splitlines():
                    entry_type,separator,name=line.partition('\t')
                    path=Path(name)
                    if separator!='\t' or entry_type!='file' or path.parent!=Path('/diag') or path.name not in allowed:
                        result['errors'].append('unexpected capture file:'+str(path));continue
                    listed.add(path.name)
                for name in sorted(required-listed):result['errors'].append('source unavailable:'+name)
                for name in sorted(listed):
                    try:
                        used=sum(row.get('sealed_bytes',0) for row in result['files'].values())
                        with SnapshotReader(before,name,deadline) as reader:
                            receipt=freeze_prefix(reader,destination/name,deadline=deadline,maximum_bytes=MAX_BYTES-used)
                            receipt['transport_recoveries']=reader.recoveries if hasattr(reader,'recoveries') else []
                        result['files'][name]=receipt
                    except SnapshotIncomplete as exc:
                        result['files'][name]=exc.receipt;result['errors'].append(name+': '+str(exc))
                    except Exception as exc:
                        result['errors'].append(name+': '+str(exc))
                observed=self._pod(kind,cleanup=True,timeout=min(5.,max(.01,deadline-time.monotonic()))) if ':' not in key else self._pods(kind,cleanup=True,timeout=min(5.,max(.01,deadline-time.monotonic())))[key]
                result['errors']+=pod_errors(before,observed)
            except Exception as exc:
                result['errors'].append(str(exc))
            result['complete']=not result['errors'] and all(row.get('complete') for row in result['files'].values())
            return key,result
        with ThreadPoolExecutor(max_workers=min(4,max(1,len(self.pods)))) as pool:
            components=dict(pool.map(freeze_one,self.pods.items()))
        self.frozen_snapshots={'version':2,'components':components,'errors':[
            key+': '+error for key,result in components.items() for error in result['errors']],
            'finished_at':now(),'allowance_seconds':30}
        self.frozen_snapshots['complete']=all(row['complete'] for row in components.values())
        self.state['finalization']=self.frozen_snapshots
        r.write(self.root/'snapshot-finalization.json',self.frozen_snapshots)
        return self.frozen_snapshots

    def _archive_attempt(self,kind,attempt):
        key=kind;kind=key.split(':',1)[0]
        before=self.pods[key];after=self._pods(kind,cleanup=True)[key] if ':' in key else self._pod(kind,cleanup=True);dest=self._destination(key)
        r.write(dest/'inventory-after.json',self._get('get','pods','-n',NS,cleanup=True));r.write(dest/'pod-after.json',after)
        errors=pod_errors(before,after)
        archive=dest/('raw-files-attempt-'+str(attempt)+'.tar')
        raw=dest/('raw-attempt-'+str(attempt))
        if self.settings.get('capture_finalization_version')==2:
            frozen=self.freeze_snapshots()['components'][key]
            raw=Path(frozen['raw_directory'])
            with tarfile.open(archive,'x') as tar:
                for file in sorted(raw.iterdir()):
                    if not file.is_file() or file.is_symlink():raise ValueError('unsafe frozen capture file')
                    tar.add(file,arcname=file.name,recursive=False)
            result=inspect_directory(raw);errors+=result['errors']+frozen['errors']
            result['finalization']=frozen
        else:
            with archive.open('xb') as handle:
                subprocess.run(['kubectl','exec','-n',NS,before['metadata']['name'],'-c','diag-reader','--','tar','cf','-','-C','/diag','.'],stdout=handle,stderr=subprocess.PIPE,check=True,timeout=90)
            result=extract_archive(archive,raw);errors+=result['errors']
        result['raw_directory']=str(raw)
        required=['metrics.json']+(['receiver-input.ndjson'] if kind=='agent' else ['traces.json'])
        for filename in required:
            file=raw/filename
            if not file.exists() or not file.stat().st_size:errors.append('source unavailable:'+filename)
        bounds=self._window_bounds()
        plan=next((p for p in self.plans if p['kind']==kind),{})
        interval=collection_interval(plan) if plan else 30.
        result['coverage']={}
        for signal in ('Metrics',)+(('Spans',) if kind=='gateway' else ()):
            try:
                activity,ids=self._trace_activity(bounds) if signal=='Spans' else (bounds,None)
                slack=float(self.settings.get('trace_capture_boundary_slack_seconds',15)) if signal=='Spans' else interval
                coverage=signal_coverage(raw,signal,activity,slack,ids)
                result['coverage'][signal]=coverage;errors+=coverage['errors']
            except (ValueError,UnicodeError):errors.append('invalid raw '+signal+' JSON or activity metadata')
        result['capture_interval']={'started_at':self.started_at,'archived_at':now(),'measurement_bounds':bounds}
        receiver=raw/'receiver-input.ndjson'
        if receiver.exists():
            plan=next((p for p in self.plans if p['kind']==kind),{})
            try:errors+=receiver_errors([json.loads(line) for line in receiver.read_text().splitlines()],before['metadata']['uid'],plan,bounds,**({'node_name':before['spec']['nodeName']} if ':' in key else {}))
            except (ValueError,KeyError):errors.append('invalid receiver row')
        for file in raw.glob('*errors*'):
            if file.stat().st_size:errors.append('runtime capture errors:'+file.name)
        result.update(errors=sorted(set(errors)),collector_uid=before['metadata']['uid'],node_name=before['spec'].get('nodeName'),pod=before['metadata']['name'])
        return result
    def _archive(self,kind):
        dest=self._destination(kind);attempts=[];result=None
        maximum_attempts=1 if self.settings.get('capture_finalization_version')==2 else 3
        for attempt in range(1,maximum_attempts+1):
            try:
                result=self._archive_attempt(kind,attempt)
                attempts.append({'attempt':attempt,**result})
                r.write(dest/('archive-attempt-'+str(attempt)+'-status.json'),attempts[-1])
                # Restart/rotation or missing source cannot be repaired by recopying.
                retry=any('invalid' in error for error in result['errors'])
                if not result['errors'] or not retry:break
            except Exception as exc:
                result=None;attempts.append({'attempt':attempt,'error':str(exc)})
                r.write(dest/('archive-attempt-'+str(attempt)+'-status.json'),attempts[-1])
            if attempt<maximum_attempts:time.sleep(1)
        if result is None:result={'errors':['all archive attempts failed'],'files':[]}
        raw_directory=result.pop('raw_directory',None)
        if raw_directory:
            raw=Path(raw_directory)
            if raw.exists():
                retained=dest/'raw';raw.rename(retained)
                if self.settings.get('capture_finalization_version')==2:
                    frozen=self.frozen_snapshots['components'][kind]
                    frozen['staging_raw_directory']=str(raw)
                    frozen['raw_directory']=str(retained)
                    for row in attempts:
                        if row.get('raw_directory')==str(raw):row['raw_directory']=str(retained)
                        r.write(dest/('archive-attempt-'+str(row['attempt'])+'-status.json'),row)
                    r.write(self.root/'snapshot-finalization.json',self.frozen_snapshots)
        result['attempts']=attempts;result['bounded_export_attempts']=maximum_attempts;result['max_local_export_bytes']=6*MAX_BYTES
        self.state['components'][kind]=result
        r.write(dest/'capture-status.json',result)
    def _check_source_collectors(self):
        sources=getattr(self.capture,'source_collectors',[])
        inventories={}
        if self.settings.get('expected_nodes',1)>1:
            inventory=self._get('get','nodes',cleanup=True)
            r.write(self.root/'nodes-after.json',inventory)
            current_nodes=ready_nodes(inventory,self.settings['expected_nodes'])
            current_uids={p['metadata']['name']:p['metadata']['uid'] for p in inventory['items']}
            if current_nodes!=self.nodes or current_uids!=self.node_uids:raise ValueError('Ready node membership or UID changed')
        for before in sources:
            namespace=before['metadata']['namespace']
            if namespace not in inventories:
                inventories[namespace]=self._get('get','pods','-n',namespace,cleanup=True)
                r.write(self.root/'source-collectors'/('inventory-after-'+namespace+'.json'),inventories[namespace])
            current=next((p for p in inventories[namespace]['items'] if p['metadata']['name']==before['metadata']['name']),None)
            errors=pod_errors(before,current) if current else ['source collector pod UID disappeared']
            self.state.setdefault('source_collectors',[]).append({'node_name':before.get('spec',{}).get('nodeName'),'namespace':namespace,'pod':before['metadata']['name'],'collector_uid':before['metadata']['uid'],'errors':errors})
            if self.settings.get('expected_nodes',1)>1 and current and not pod_ready(current):errors.append('source collector not Ready')
            for error in errors:self.state['errors'].append('source collector '+before['metadata']['name']+':'+error)
        if self.settings.get('expected_nodes',1)>1:
            source_ids={o['uid'] for p in sources for o in p.get('metadata',{}).get('ownerReferences',[]) if o.get('controller')}
            current=[p for inventory in inventories.values() for p in inventory['items'] if any(o.get('controller') and o.get('uid') in source_ids for o in p.get('metadata',{}).get('ownerReferences',[]))]
            validate_source_coverage(current,self.nodes)
            if {p['metadata']['uid'] for p in current}!={p['metadata']['uid'] for p in sources}:raise ValueError('source collector membership changed')
    def _restore(self):
        from .capture_watchdog import run
        manager=self
        class LocalAPI:
            def call(self,path,method='GET',value=None):
                if method=='GET':return json.loads(manager._cleanup_kube('get','--raw',path).stdout)
                # kubectl raw replace does not accept JSON Patch; use resource/name.
                parts=path.split('/');resource=parts[-2]+'/'+parts[-1]
                return json.loads(manager._cleanup_kube('patch',resource,'-n',NS,'--type=json','-p',json.dumps(value),'-o','json').stdout)
        if not self.plans or not hasattr(self,'snapshot'):return {'restored':True,'resources':[]}
        result=run(self.snapshot,LocalAPI(),wait=False)
        if result['restored']:
            for plan in self.plans:self._cleanup_kube('rollout','status',self._resource(plan['kind']),'-n',NS,'--timeout=240s',timeout=250)
        return result
    def _cancel_watchdog(self):
        if not self.watchdog_installed:return
        for resource in ('job','rolebinding','role','serviceaccount','configmap'):
            response=self._cleanup_kube('get',resource,self.name,'-n',NS,'--ignore-not-found','-o','json')
            if not response.stdout.strip():continue
            current=json.loads(response.stdout)
            if current['metadata'].get('labels',{}).get(OWNER)!=self.owner:raise ValueError('watchdog auxiliary ownership changed')
            if resource=='configmap':
                r.write(self.root/'watchdog-terminal-status.json',{'data':current.get('data',{}).get('status.json'),'uid':current['metadata']['uid'],'normal_restore':self.state.get('cleanup')})
            # The name is unique per window; refuse label changes before deletion.
            cascade=['--cascade=foreground'] if resource=='job' else []
            self._cleanup_kube('delete',resource,self.name,'-n',NS,'--wait=true','--timeout=60s',*cascade)
        self.watchdog_installed=False
    def stop(self, *, archive=True):
        if self.stopped:return self.status()
        try:
            if self.capture:
                try:self.capture.stop()
                except Exception as exc:self.state['errors'].append('sampler-stop:'+str(exc))
                self.state['errors']+=self.capture.sampling_errors
                if isinstance(getattr(self.capture,'sample_writers',None),dict):
                    try:self.state['sampling']=self.capture._sampling_status()
                    except Exception as exc:self.state['errors'].append('sampling status:'+str(exc))
                    self.state['sampling_integrity']={}
                    for filename,writer in self.capture.sample_writers.items():
                        try:self.state['sampling_integrity'][filename]=writer.validate()
                        except Exception as exc:
                            self.state['sampling_integrity'][filename]={'valid':False,'error':str(exc)}
                            self.state['errors'].append({'source':'sampling-integrity','path':filename,'error':str(exc)})
                for stream in self.capture.log_captures:
                    status=stream.status()
                    if status.get('errors') or status.get('returncode') or status.get('exited_before_stop'):self.state['errors'].append({'source':'logs','status':status})
                if any(t.is_alive() for t in self.capture.sampler_threads):self.state['errors'].append('sampler still running')
                try:self._check_source_collectors()
                except Exception as exc:self.state['errors'].append('source collector inventory:'+str(exc))
            for kind in self.pods if archive else ():
                try:self._archive(kind)
                except Exception as exc:self.state['errors'].append('archive '+kind+':'+str(exc))
        finally:
            self.state['instrumentation']['cleanup_started_at']=now()
            try:
                restored=self._restore();self.state['cleanup']=restored
                if restored.get('restored'):self._cancel_watchdog()
                else:self.state['errors'].append('guarded restoration refused; watchdog retained')
            except Exception as exc:self.state['errors'].append('cleanup:'+str(exc));self.state['cleanup']={'restored':False,'error':str(exc)}
            # Completion is separate from evidence validity: failed samples may
            # coexist with fully stopped workers, and errors alone prove neither.
            capture = self.capture
            threads = [] if capture is None else [thread for thread in
                [getattr(capture, 'thread', None), *getattr(capture, 'sampler_threads', [])] if thread is not None]
            workers = [{'name':thread.name, 'alive':thread.is_alive()} for thread in threads]
            streams = [] if capture is None else [{'stream':stream.status().get('stream'),
                'stopped':stream.status().get('stopped') is True,
                'worker_alive':bool(getattr(stream, 'thread', None) and stream.thread.is_alive())}
                for stream in getattr(capture, 'log_captures', [])]
            proxies = {} if capture is None else {name:getattr(capture, name) for name in
                ('kubelet_proxy_lifecycle', 'log_proxy_lifecycle') if hasattr(capture, name)}
            self.state['completion'] = {'capture_present':capture is not None,
                'observed_at':now(), 'sampler_workers':workers, 'log_workers':streams,
                'proxy_processes':proxies}
            self.state['workers_stopped'] = not any(row['alive'] for row in workers) and all(
                row['stopped'] and not row['worker_alive'] for row in streams)
            self.state['proxy_stopped'] = all(row.get('stopped') is True for row in proxies.values())
            self.stopped=True;self.state['finished_at']=now();self.state['instrumentation']['cleanup_finished_at']=now()
            self.state['valid']=bool(self.started_at and len(self.state['components'])==self.settings.get('expected_nodes',1)+1 and not self.state['errors'] and all(not c['errors'] for c in self.state['components'].values()) and self.state.get('cleanup',{}).get('restored'))
            r.write(self.root/'status.json',self.state)
        return self.status()
