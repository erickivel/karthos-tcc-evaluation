"""Admit resource helpers before capture; release stimulus at generator start."""
from __future__ import annotations

import json
import math
import time

from .diagnostics.resources import build_resources_both, bind_network_target

SETUP_SECONDS = 3000
GATED_LABEL = 'evaluation.karthos.dev/prepared-resource'
GATE = """import json,os,pathlib,sys,time
pathlib.Path('/tmp/eval-prepared').touch()
while not pathlib.Path('/tmp/eval-start').exists():
    time.sleep(.05)
pathlib.Path('/tmp/eval-stimulus-start.json').write_text(json.dumps({'started_at_ns':time.time_ns()}))
os.execvp(sys.argv[1],sys.argv[1:])
"""
RELEASE = """import json,pathlib,time
if pathlib.Path('/tmp/eval-start').exists():
    raise RuntimeError('prepared stimulus already released')
requested=time.time_ns()
pathlib.Path('/tmp/eval-start').touch()
record=pathlib.Path('/tmp/eval-stimulus-start.json')
end=time.monotonic()+5
while not record.exists() and time.monotonic()<end:
    time.sleep(.02)
if not record.exists():
    raise RuntimeError('prepared stimulus did not acknowledge release')
print(json.dumps({'release_requested_at_ns':requested,**json.loads(record.read_text())}))
"""


def _owned_pod(ctx, obj):
    meta = obj['metadata']
    admitted = ctx.json('get', 'pod', meta['name'], '-n', meta['namespace'])
    actual = admitted.get('metadata', {})
    if (not actual.get('uid') or actual.get('deletionTimestamp')
            or any(actual.get('labels', {}).get(k) != v for k,v in meta['labels'].items())
            or (meta.get('uid') and actual['uid'] != meta['uid'])
            or admitted.get('spec', {}).get('nodeName') != obj['spec']['nodeSelector']['kubernetes.io/hostname']):
        raise ValueError('prepared helper ownership, UID or placement changed: '+meta['name'])
    return admitted


def prepare(ctx, driver, spec, token, scratch, objects):
    """Create helpers without starting CPU/fio/network-client activity.

    Returns preparation evidence. Mutates objects only to retain manifests and
    admitted Pod UIDs; caller owns UID-safe cleanup on every failure path.
    """
    started = time.time()
    remaining = ctx.deadline-ctx.config['limits']['cleanup_reserve_seconds']-started
    if remaining <= spec['duration_seconds']+90:
        raise TimeoutError('insufficient remaining budget to prepare resource helpers')
    lifetime = math.ceil(min(remaining, SETUP_SECONDS+spec['duration_seconds']+90))
    built = build_resources_both(spec['scenario'],driver.cell.namespace,driver.nodes,
        ctx.config['readiness']['resource_helper_image'],token,duration_seconds=spec['duration_seconds'])
    for obj in built:
        if obj['kind']=='StorageClass':
            obj['parameters']['labels']='evaluation-owner=gke-qualification-v1,evaluation-run='+ctx.run_id
        if obj['kind']!='Pod':
            continue
        obj['metadata'].setdefault('annotations',{})['sidecar.istio.io/inject']='false'
        obj['spec']['activeDeadlineSeconds']=lifetime
        obj['metadata'].setdefault('annotations',{})['evaluation.karthos.dev/activity-seconds']=str(spec['duration_seconds'])
        for container in obj['spec']['containers']:
            if container['name']=='volume-reference':
                args=container['args'];args[args.index('--duration')+1]=str(lifetime)
            elif not obj['metadata']['name'].endswith('-iperf-server'):
                original=container['command']+container.get('args',[])
                container['command']=['python3','-c',GATE]
                container['args']=original
                container['readinessProbe']={'exec':{'command':['python3','-c',
                    "from pathlib import Path;raise SystemExit(0 if Path('/tmp/eval-prepared').exists() else 1)"]},
                    'periodSeconds':1,'timeoutSeconds':1,'failureThreshold':120}
                obj['metadata']['labels'][GATED_LABEL]='true'
    objects.extend(built)
    ctx.write(scratch/'stimulus-objects.json',objects)
    target = None
    if spec['scenario']=='network':
        server,client=built
        driver._apply_objects([server])
        ctx.kube('wait','--for=condition=Ready','pod/'+server['metadata']['name'],'-n',driver.cell.namespace,'--timeout=120s',timeout=130)
        admitted=_owned_pod(ctx,server)
        target=bind_network_target(server,client,admitted)
        server['metadata']['uid']=admitted['metadata']['uid']
        ctx.write(scratch/'stimulus-network-target.json',target)
        driver._apply_objects([client])
    else:
        driver._apply_objects(built)
    for obj in built:
        meta=obj['metadata']
        if obj['kind']=='PersistentVolumeClaim':
            ctx.kube('wait','--for=jsonpath={.status.phase}=Bound','pvc/'+meta['name'],'-n',meta['namespace'],'--timeout=120s',timeout=130)
    observed=[]
    for obj in built:
        if obj['kind']!='Pod':continue
        meta=obj['metadata']
        ctx.kube('wait','--for=condition=Ready','pod/'+meta['name'],'-n',meta['namespace'],'--timeout=120s',timeout=130)
        pod=_owned_pod(ctx,obj)
        statuses=pod.get('status',{}).get('containerStatuses',[])
        expected={c['name'] for c in obj['spec']['containers']}
        if (pod.get('status',{}).get('phase')!='Running' or {c['name'] for c in statuses}!=expected
                or any(not c.get('ready') or not c.get('state',{}).get('running') or not c.get('imageID') for c in statuses)):
            raise ValueError('prepared helper containers not ready: '+meta['name'])
        meta['uid']=pod['metadata']['uid']
        observed.append({'pod':meta['name'],'uid':meta['uid'],'node_name':pod['spec']['nodeName'],'containers':statuses})
    if spec['scenario']=='storage':
        from .provision import arm_watchdog
        arm_watchdog(ctx)
    result={'started_at':started,'finished_at':time.time(),'pods':observed,'network_target':target,
            'helper_lifetime_seconds':lifetime,'gated_stimulus':True}
    ctx.write(scratch/'prepared-resources.json',result)
    ctx.write(scratch/'stimulus-objects.json',objects)
    return result


def release(ctx, driver, objects, path):
    """Release only exact prepared Pod UIDs after caller observes WINDOW."""
    result={'requested_at':time.time(),'pods':[]}
    for obj in objects:
        if obj['kind']!='Pod' or obj['metadata'].get('labels',{}).get(GATED_LABEL)!='true':continue
        if not obj['metadata'].get('uid'):raise ValueError('prepared helper UID unavailable')
        pod=_owned_pod(ctx,obj)
        if pod.get('status',{}).get('phase')!='Running':raise ValueError('prepared helper stopped before release')
        remaining=pod['spec'].get('activeDeadlineSeconds',0) - (time.time()-__import__('datetime').datetime.fromisoformat(pod['status']['startTime'].replace('Z','+00:00')).timestamp())
        required=int(obj['metadata']['annotations']['evaluation.karthos.dev/activity-seconds'])+60
        if remaining<required:raise RuntimeError('prepared helper lifetime nearly exhausted before release')
        response=ctx.kube('exec','-n',obj['metadata']['namespace'],obj['metadata']['name'],'-c','stimulus','--','python3','-c',RELEASE,timeout=10)
        timing=json.loads(response.stdout)
        result['pods'].append({'pod':obj['metadata']['name'],'uid':obj['metadata']['uid'],**timing})
        # Retain earlier releases even when a later helper fails to release.
        ctx.write(path/'stimulus-release.json',result)
    result['finished_at']=time.time()
    ctx.write(path/'stimulus-release.json',result)
    return result
