"""Capture same-name Pod identity and placement on a disposable kind cluster."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import time

from ..consumers.api_only import APIConsumer
from ..gke.models import sha, write
from ..gke.thesis import openapi_projection


def now():
    return datetime.now(timezone.utc).isoformat()


def wait_for(read, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if value is not None:
            return value
        time.sleep(min(3, max(0, deadline-time.monotonic())))
    raise TimeoutError(label)


@contextmanager
def api_forward(kube_args, contract, path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    with (path/'port-forward.log').open('w') as log:
        child = subprocess.Popen([*kube_args,'-n','karthos-system','port-forward',
                                  'deployment/karthos-backend',str(port)+':8080'],
                                 stdout=log,stderr=subprocess.STDOUT)
        try:
            client = APIConsumer('http://127.0.0.1:'+str(port),timeout=5,contract=contract)
            def ready():
                if child.poll() is not None:
                    raise RuntimeError('backend port-forward exited')
                return True if 'Forwarding from 127.0.0.1' in (path/'port-forward.log').read_text() else None
            wait_for(ready,15,'backend port-forward did not bind')
            yield client
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)


def capture(kube_args, image, client, out):
    def kube(*args, document=None):
        result = subprocess.run([*kube_args,*args],input=json.dumps(document) if document else None,
                                text=True,check=True,capture_output=True,timeout=45)
        return json.loads(result.stdout) if args[-2:] == ('-o','json') else result.stdout
    inventory = kube('get','nodes','-o','json')
    nodes = sorted(row['metadata']['name'] for row in inventory['items'])
    if len(nodes) != 2:
        raise ValueError('two isolated kind nodes required for distinct placement')
    namespace = 'thesis-identity'
    label = {'evaluation.karthos.dev/owner':'thesis-local'}
    owner = kube('create','-f','-','-o','json',document={
        'apiVersion':'v1','kind':'Namespace',
        'metadata':{'name':namespace,'labels':label}})
    def pod(node):
        return kube('create','-f','-','-o','json',document={
            'apiVersion':'v1','kind':'Pod',
            'metadata':{'name':'stable-pod','namespace':namespace,'labels':label,
                        'annotations':{'sidecar.istio.io/inject':'false'}},
            'spec':{'nodeName':node,'restartPolicy':'Never','automountServiceAccountToken':False,
                    'containers':[{'name':'fixture','image':image,'command':['node','-e','setTimeout(()=>{},1800000)']}]}})
    def observe(placed):
        def check():
            stamp = now()
            graph = client.graph(at=stamp)
            if graph['state'] != 'observed':
                return None
            app = [row for row in graph['nodes'] if row.get('namespace') == namespace and row.get('name') == 'stable-pod' and row['kind'] == 'pod']
            node = [row for row in graph['nodes'] if row.get('name') == placed['spec']['nodeName'] and row['kind'] == 'node']
            if len(app) != 1 or len(node) != 1:
                return None
            edges = [edge for edge in graph['edges'] if edge['relation'] == 'runs_on' and edge['source'] == app[0]['id']]
            if not any(edge['target'] == node[0]['id'] for edge in edges):
                return None
            return {'name':'stable-pod','uid':placed['metadata']['uid'],'canonical_id':app[0]['id'],
                    'node_id':node[0]['id'],'placement_edges':edges,'snapshot':graph['snapshot']['id'],
                    'at':stamp,'graph':graph}
        return wait_for(check,350,'distinct retained placement not observed')
    try:
        first = pod(nodes[0])
        old = observe(first)
        write(out/'placement-before.json',old)
        kube('delete','--raw','/api/v1/namespaces/'+namespace+'/pods/stable-pod','-f','-',
             document={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':first['metadata']['uid']},'gracePeriodSeconds':0})
        kube('wait','--for=delete','pod/stable-pod','-n',namespace,'--timeout=30s')
        new = observe(pod(nodes[1]))
        write(out/'placement-after.json',new)
        retained = client.graph(at=old['at'])
        write(out/'placement-retained.json',retained)
        if retained.get('snapshot',{}).get('id') != old['snapshot']:
            raise ValueError('the original placement snapshot could not be queried again')
        return [{k:v for k,v in row.items() if k!='graph'} for row in (old,new)]
    finally:
        kube('delete','--raw','/api/v1/namespaces/'+namespace,'-f','-',
             document={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':owner['metadata']['uid']}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig',required=True,type=Path)
    parser.add_argument('--context',required=True)
    parser.add_argument('--image',required=True,help='immutable service-b-node fixture image')
    parser.add_argument('--openapi',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    args = parser.parse_args()
    if not args.context.startswith('kind-karthos-thesis-') or '@sha256:' not in args.image:
        parser.error('requires an explicitly named disposable thesis kind context and immutable fixture image')
    kube_args = ['kubectl','--kubeconfig',str(args.kubeconfig),'--context',args.context]
    context = subprocess.run([*kube_args,'config','current-context'],capture_output=True,text=True,check=True,timeout=10)
    if context.stdout.strip() != args.context:
        parser.error('kubeconfig does not select the requested disposable context')
    args.out.mkdir(exist_ok=False)
    contract = openapi_projection(args.openapi,sha(args.openapi))
    with api_forward(kube_args,contract,args.out) as client:
        write(args.out/'replacement.json',capture(kube_args,args.image,client,args.out))


if __name__ == '__main__':
    main()
