"""Bounded diagnostic manifests only; callers own admission and execution."""
import json
from ipaddress import ip_address
import re
from ..models import identifier

OWNER='gke-qualification-v1'
OWNER_LABEL='evaluation.karthos.dev/owner'
RUN_LABEL='evaluation.karthos.dev/run-id'

# A connecting probe consumes iperf3's one-off server session.
IPERF_READY = """import sys
for name in ('tcp', 'tcp6'):
    try:
        with open('/proc/net/' + name) as sockets:
            next(sockets, None)
            for row in sockets:
                fields = row.split()
                if fields[1].endswith(':1451') and fields[3] == '0A':
                    sys.exit(0)
    except FileNotFoundError:
        continue
sys.exit(1)
"""


def verify_network_result(text):
    """iperf3 3.12 can exit zero after writing an error in JSON mode."""
    result = json.loads(text)
    if result.get('error'):
        raise ValueError('iperf3 reported an error: '+str(result['error']))
    transferred = result.get('end', {}).get('sum_received', {}).get('bytes')
    if type(transferred) is not int or transferred <= 0:
        raise ValueError('iperf3 result has no completed transfer')



def _image(image):
    if not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',str(image)):
        raise ValueError('reviewed immutable helper image digest required')


def labels(run_id):
    identifier(run_id)
    return {OWNER_LABEL:OWNER,RUN_LABEL:run_id}


def _nodes(nodes):
    if (len(nodes)!=3 or {n.get('slot') for n in nodes}!={'infra','app-a','app-b'}
            or len({n.get('name') for n in nodes})!=3 or any(not n.get('name') for n in nodes)):
        raise ValueError('exactly three distinct infra/app-a/app-b nodes required')
    return {n['slot']:n for n in nodes}


def build_resources(scenario, namespace, nodes, image, run_id, duration_seconds=300):
    """CPU/memory, bounded PVC IO, or app-a→app-b iperf3 manifests.

    These commands generate stimulus. Actual proc/kubelet/statvfs readings and
    tool output are reference evidence; configured targets are not measured truth.
    Helper image must already contain reviewed stress-ng/fio/iperf3 versions.
    """
    identifier(namespace);_image(image);owner=labels(run_id);slots=_nodes(nodes)
    if scenario not in ('cpu-memory','storage','network') or not 1<=duration_seconds<=300:
        raise ValueError('supported bounded resource scenario/duration required')
    prefix='diag-'+run_id

    def meta(name): return {'name':name,'namespace':namespace,'labels':dict(owner)}
    def pod(name,slot,command,args,*,mounts=(),volumes=()):
        return {'apiVersion':'v1','kind':'Pod','metadata':meta(name),'spec':{
            'automountServiceAccountToken':False,'restartPolicy':'Never','activeDeadlineSeconds':duration_seconds+60,
            'terminationGracePeriodSeconds':10,'nodeSelector':{'kubernetes.io/hostname':slots[slot]['name']},
            'securityContext':{'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532,'fsGroup':65532},
            'containers':[{'name':'stimulus','image':image,'imagePullPolicy':'IfNotPresent','command':command,'args':args,
                'resources':{'requests':{'cpu':'250m','memory':'384Mi'},'limits':{'cpu':'1500m','memory':'768Mi'}},
                'securityContext':{'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
                'volumeMounts':list(mounts)}],'volumes':list(volumes)}}
    seconds=str(duration_seconds)
    if scenario=='cpu-memory':
        worker=pod(prefix+'-cpu','app-a',['stress-ng'],['--cpu','1','--cpu-load','50','--vm','1',
                '--vm-bytes','256M','--vm-keep','--timeout',seconds+'s','--metrics-brief'])
        # The helper image's default workdir is root-owned.
        worker['spec']['containers'][0]['workingDir']='/tmp'
        return [worker]
    if scenario=='storage':
        name=prefix+'-disk'
        sc={'apiVersion':'storage.k8s.io/v1','kind':'StorageClass','metadata':{'name':name,'labels':dict(owner)},
            'provisioner':'pd.csi.storage.gke.io','reclaimPolicy':'Delete','volumeBindingMode':'WaitForFirstConsumer',
            'allowVolumeExpansion':False,'parameters':{'type':'pd-balanced',
            'labels':'evaluation-owner='+OWNER+',evaluation-run='+run_id}}
        pvc={'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':meta(name),'spec':{
            'accessModes':['ReadWriteOnce'],'storageClassName':name,'resources':{'requests':{'storage':'2Gi'}}}}
        worker=pod(prefix+'-fio','app-a',['fio'],['--name=bounded-io','--filename=/owned/reference.bin',
            '--size=256M','--rw=randrw','--rwmixread=50','--bs=4k','--iodepth=1','--rate=4M',
            '--ioengine=psync','--direct=1','--time_based','--runtime='+seconds,'--output-format=json'],
            mounts=[{'name':'owned','mountPath':'/owned'}],volumes=[{'name':'owned','persistentVolumeClaim':{'claimName':name}}])
        # Same-container sampler sees this exact PVC/device without raw mounts or credentials.
        worker['spec']['containers'].append({'name':'volume-reference','image':image,
            'command':['python3','/opt/evaluation/infrastructure/helpers/reference_sampler.py'],
            'args':['--volume-only','--duration',str(duration_seconds+30)],
            'env':[{'name':'NODE_NAME','valueFrom':{'fieldRef':{'fieldPath':'spec.nodeName'}}},
                   {'name':'NODE_UID','value':slots['app-a'].get('uid','')},
                   {'name':'POD_UID','valueFrom':{'fieldRef':{'fieldPath':'metadata.uid'}}},
                   {'name':'RUN_ID','value':run_id},
                   {'name':'OWNED_MOUNTS','value':json.dumps([{'mount':'/owned','owned':True}])}],
            'resources':{'requests':{'cpu':'10m','memory':'32Mi'},'limits':{'cpu':'100m','memory':'64Mi'}},
            'securityContext':{'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
            'volumeMounts':[{'name':'owned','mountPath':'/owned','readOnly':True}]})
        return [sc,pvc,worker]
    server=pod(prefix+'-iperf-server','app-b',['iperf3'],['--server','--one-off','--json'])
    server['metadata']['labels']['evaluation.karthos.dev/network-role']='server'
    server['spec']['containers'][0]['ports']=[{'containerPort':5201}]
    server['spec']['containers'][0]['readinessProbe']={
        'exec':{'command':['python3','-c',IPERF_READY]},
        'periodSeconds':1,'timeoutSeconds':1,'failureThreshold':120}
    client=pod(prefix+'-iperf-client','app-a',['iperf3'],
                ['--time',seconds,'--bitrate','10M','--connect-timeout','10000','--json'])
    return [server,client]


def bind_network_target(server, client, admitted):
    expected = server['metadata']
    meta, status = admitted.get('metadata', {}), admitted.get('status', {})
    host = server['spec']['nodeSelector']['kubernetes.io/hostname']
    if (any(meta.get(key) != expected[key] for key in ('namespace', 'name'))
            or not meta.get('uid') or meta.get('deletionTimestamp')
            or any(meta.get('labels', {}).get(k) != v for k, v in expected['labels'].items())
            or admitted.get('spec', {}).get('nodeName') != host
            or client['spec']['nodeSelector']['kubernetes.io/hostname'] == host
            or status.get('phase') != 'Running'
            or not any(c.get('type') == 'Ready' and c.get('status') == 'True'
                       for c in status.get('conditions', []))):
        raise ValueError('network target must be the exact Ready owned server on the opposite node')
    address = str(ip_address(status.get('podIP', '')))
    args = client['spec']['containers'][0]['args']
    args[0:0] = ['--client', address]
    return {'pod': meta['name'], 'pod_uid': meta['uid'], 'pod_ip': address, 'node_name': host}


def build_reference_daemonset(namespace,image,run_id,period=1,*,nodes=None):
    """Host read-only sampler. Supply actual node name→UID inventory for identity."""
    identifier(namespace);_image(image);owner=labels(run_id)
    if not .1<=period<=60: raise ValueError('reference cadence must be .1..60 seconds')
    uid_map={}
    if nodes is not None:
        _nodes(nodes)
        if any(not n.get('uid') for n in nodes): raise ValueError('node UID inventory required')
        uid_map={n['name']:n['uid'] for n in nodes}
    from ..reference_spool import LAUNCHER, SPOOL_DIR
    selector=dict(owner,**{'evaluation.karthos.dev/component':'host-reference'})
    spec={'hostNetwork':True,'dnsPolicy':'ClusterFirstWithHostNet','automountServiceAccountToken':False,
        'terminationGracePeriodSeconds':5,
        'containers':[{'name':'reference','image':image,'command':['python3','-c',LAUNCHER,'python3','-u','/opt/evaluation/infrastructure/helpers/reference_sampler.py'],
            'args':['--period',str(period)],'env':[{'name':'NODE_NAME','valueFrom':{'fieldRef':{'fieldPath':'spec.nodeName'}}},
            {'name':'POD_UID','valueFrom':{'fieldRef':{'fieldPath':'metadata.uid'}}},
            {'name':'NODE_UID_MAP','value':json.dumps(uid_map,sort_keys=True)},{'name':'RUN_ID','value':run_id}],
            'resources':{'requests':{'cpu':'20m','memory':'32Mi'},'limits':{'cpu':'200m','memory':'64Mi'}},
            'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
            'volumeMounts':[{'name':'host','mountPath':'/hostfs','readOnly':True},{'name':'reference-spool','mountPath':SPOOL_DIR}]}],
        'volumes':[{'name':'host','hostPath':{'path':'/','type':'Directory'}},{'name':'reference-spool','emptyDir':{'sizeLimit':'256Mi'}}]}
    if nodes is not None:
        spec['affinity']={'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[
            {'matchFields':[{'key':'metadata.name','operator':'In','values':[n['name']]}]} for n in nodes]}}}
    return {'apiVersion':'apps/v1','kind':'DaemonSet','metadata':{'name':'diag-'+run_id+'-reference','namespace':namespace,'labels':owner},
        'spec':{'selector':{'matchLabels':selector},'template':{'metadata':{'labels':selector},'spec':spec}}}


def build_resources_both(scenario, namespace, nodes, image, run_id, duration_seconds=300):
    """Independent bounded CPU/storage stimulus on both app workers; network once."""
    import copy
    first=build_resources(scenario,namespace,nodes,image,run_id,duration_seconds)
    if scenario=='network': return first
    second=copy.deepcopy(first)
    renames={obj['metadata']['name']:obj['metadata']['name']+'-app-b' for obj in second}
    slots=_nodes(nodes)
    for obj in second:
        obj['metadata']['name']=renames[obj['metadata']['name']]
        if obj['kind']=='PersistentVolumeClaim':
            obj['spec']['storageClassName']=renames[obj['spec']['storageClassName']]
        if obj['kind']=='Pod':
            obj['spec']['nodeSelector']['kubernetes.io/hostname']=slots['app-b']['name']
            for volume in obj['spec'].get('volumes',[]):
                if 'persistentVolumeClaim' in volume:
                    volume['persistentVolumeClaim']['claimName']=renames[volume['persistentVolumeClaim']['claimName']]
            for container in obj['spec']['containers']:
                for env in container.get('env',[]):
                    if env['name']=='NODE_UID': env['value']=slots['app-b'].get('uid','')
    return first+second
