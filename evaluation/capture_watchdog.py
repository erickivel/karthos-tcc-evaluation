"""Independent in-cluster deadline cleanup; Python standard library only.

The Job's service account can read/patch only the two pre-existing collector
workloads/configmaps, its status ConfigMap and the evaluation ownership marker.
Restoration is refused after any UID, owner, template or config-data change.
"""
from __future__ import annotations
import hashlib
import json
import os
import ssl
import time
import urllib.request
import urllib.error
from pathlib import Path

OWNER = 'evaluation.karthos.dev/capture-owner'
GATEWAY_RESTART = 'evaluation.karthos.dev/restart-window'

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def restore_patch(plan, target, current):
    before, after = plan['before_'+target], plan['after_'+target]
    if current['metadata']['uid'] != before['metadata']['uid']:
        raise ValueError('resource UID changed; refusing restoration')
    path = '/data' if target == 'configmap' else '/spec/template'
    field = current['data'] if target == 'configmap' else current['spec']['template']
    old = before['data'] if target == 'configmap' else before['spec']['template']
    expected = after['data'] if target == 'configmap' else after['spec']['template']
    restart_token = plan.get('gateway_restart_token')
    if target == 'workload' and restart_token and plan.get('kind') == 'gateway':
        metadata = expected['metadata']
        annotations = dict(metadata.get('annotations', {}))
        annotations[GATEWAY_RESTART] = restart_token
        restarted = dict(expected, metadata=dict(metadata, annotations=annotations))
        if field == restarted:
            expected = restarted
    # Untouched (or already restored) is safe and requires no write.
    if field == old:
        return []
    annotations = (current['metadata'] if target == 'configmap' else field.get('metadata', {})).get('annotations', {})
    if annotations.get(OWNER) != plan['owner'] or fingerprint(field) != fingerprint(expected):
        raise ValueError('owned content changed; preserving user changes')
    patch = [{'op':'test', 'path':'/metadata/uid', 'value':before['metadata']['uid']},
             {'op':'test', 'path':path, 'value':expected},
             {'op':'replace', 'path':path, 'value':old}]
    if target == 'configmap':
        escaped=OWNER.replace('~','~0').replace('/','~1')
        patch.insert(1, {'op':'test','path':'/metadata/annotations/'+escaped,'value':plan['owner']})
        previous=before['metadata'].get('annotations',{}).get(OWNER)
        patch.append({'op':'remove','path':'/metadata/annotations/'+escaped} if previous is None else
                     {'op':'replace','path':'/metadata/annotations/'+escaped,'value':previous})
    return patch

class API:
    def __init__(self):
        root=Path('/var/run/secrets/kubernetes.io/serviceaccount')
        self.token=(root/'token').read_text().strip()
        self.context=ssl.create_default_context(cafile=str(root/'ca.crt'))
        self.base='https://'+os.environ['KUBERNETES_SERVICE_HOST']+':'+os.environ['KUBERNETES_SERVICE_PORT']
    def call(self,path,method='GET',value=None):
        body=None if value is None else json.dumps(value).encode()
        req=urllib.request.Request(self.base+path,data=body,method=method,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json-patch+json' if method=='PATCH' else 'application/json'})
        with urllib.request.urlopen(req,context=self.context,timeout=20) as response:return json.load(response)

def resource_path(plan,target,namespace):
    name=plan['before_'+target]['metadata']['name']
    if target=='configmap':return '/api/v1/namespaces/'+namespace+'/configmaps/'+name
    resource='daemonsets' if plan['kind']=='agent' else 'deployments'
    return '/apis/apps/v1/namespaces/'+namespace+'/'+resource+'/'+name

def run(snapshot, api, wait=True):
    if wait:
        while time.time() < snapshot['cleanup_deadline_epoch']:
            time.sleep(min(15,max(0,snapshot['cleanup_deadline_epoch']-time.time())))
    result={'owner':snapshot['owner'],'attempted_at_ns':time.time_ns(),'resources':[]}
    marker='/api/v1/namespaces/'+snapshot['namespace']+'/configmaps/karthos-evaluation-owner'
    if api.call(marker)['metadata']['uid'] != snapshot['ownership_marker_uid']:
        raise ValueError('evaluation ownership marker UID changed')
    for plan in snapshot['plans']:
        pending=[]
        try:
            # Validate both boundaries before changing either. A conflicting
            # template/config preserves the entire component for inspection.
            for target in ('configmap','workload'):
                path=resource_path(plan,target,snapshot['namespace'])
                pending.append((target,path,restore_patch(plan,target,api.call(path))))
        except Exception as exc:
            for target in ('configmap','workload'):
                result['resources'].append({'kind':plan['kind'],'target':target,'restored':False,'error':str(exc)})
            continue
        # Restore config before restarting pods with their original template.
        for target,path,patch in pending:
            row={'kind':plan['kind'],'target':target}
            try:
                if patch:api.call(path,'PATCH',patch)
                verify=api.call(path)
                if restore_patch(plan,target,verify):raise ValueError('restore verification failed')
                row['restored']=True
            except Exception as exc:row.update(restored=False,error=str(exc))
            result['resources'].append(row)
            if not row['restored']:
                if target=='configmap':result['resources'].append({'kind':plan['kind'],'target':'workload','restored':False,'error':'config restore failed; template preserved'})
                break
    result['restored']=all(x['restored'] for x in result['resources'])
    return result

if __name__=='__main__':
    snapshot=json.loads(Path('/scripts/snapshot.json').read_text());api=API()
    try:result=run(snapshot,api)
    except Exception as exc:result={'restored':False,'error':str(exc),'at_ns':time.time_ns()}
    status='/api/v1/namespaces/'+snapshot['namespace']+'/configmaps/'+snapshot['name']
    api.call(status,'PATCH',[{'op':'add','path':'/data/status.json','value':json.dumps(result)}])
    print(json.dumps(result),flush=True)
    raise SystemExit(0 if result.get('restored') else 1)
