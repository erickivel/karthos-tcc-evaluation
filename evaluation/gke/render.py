"""Evaluation-owned Helm post-rendering: placement and reviewed image identities only."""
from __future__ import annotations
import copy,hashlib,json,sys,argparse
from pathlib import Path

RESOURCE_POLICY='requests-v1'
KARTHOS_REQUESTS={
 'karthos-agent':{'cpu':'100m','memory':'256Mi'},
 'karthos-backend':{'cpu':'250m','memory':'512Mi'},
 'karthos-frontend':{'cpu':'50m','memory':'64Mi'},
 'karthos-gateway':{'cpu':'300m','memory':'512Mi'},
 'karthos-postgresql':{'cpu':'100m','memory':'128Mi'},
 'karthos-victoria-metrics-single-server':{'cpu':'300m','memory':'1Gi'},
}

def scheduling_requests(container,workload,mode,*,ordinary_init=False):
    defaults={'cpu':'10m','memory':'16Mi'} if ordinary_init else KARTHOS_REQUESTS.get(workload,{'cpu':'100m','memory':'128Mi'}) if mode=='karthos' else {'cpu':'100m','memory':'128Mi'}
    if container.get('resources') is None:container['resources']={}
    resources=container['resources']
    if resources.get('requests') is None:resources['requests']={}
    limits=resources.get('limits') or {}
    for field,value in defaults.items():
        # Match Kubernetes admission defaulting when an existing limit supplies it.
        resources['requests'].setdefault(field,limits.get(field,value))


def normalized(image):
    if '/' not in image.split('@')[0]:image='docker.io/library/'+image
    elif '.' not in image.split('/')[0] and ':' not in image.split('/')[0]:image='docker.io/'+image
    if '@' in image:
        repository,digest=image.split('@',1)
        last=repository.rsplit('/',1)[-1]
        if ':' in last:repository=repository.rsplit(':',1)[0]
        return repository+'@'+digest
    if ':' not in image.rsplit('/',1)[-1]:image+=':latest'
    return image

def transform(objects,image_lock,*,run_id,mode):
    if mode not in ('demo','karthos'):raise ValueError('unknown placement mode')
    import re
    if any(not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',str(value)) for value in image_lock.values()):raise ValueError('image lock contains mutable or malformed images')
    locked={normalized(k):v for k,v in image_lock.items()};approved={normalized(v) for v in image_lock.values()};out=[]
    def pin(image):
        n=normalized(image)
        if n in locked:return locked[n]
        if n in approved:return image
        raise ValueError('unreviewed image: '+image)
    for original in objects:
        if not original:continue
        d=copy.deepcopy(original);meta=d.setdefault('metadata',{});name=meta.get('name','')
        meta.setdefault('labels',{}).update({'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':run_id})
        kind=d.get('kind');spec=d.get('spec',{});pod=spec if kind=='Pod' else spec.get('template',{}).get('spec')
        if pod is not None:
            if kind!='Pod':spec['template'].setdefault('metadata',{}).setdefault('labels',{}).update(meta['labels'])
            podmeta=meta if kind=='Pod' else spec['template']['metadata']
            podmeta.setdefault('annotations',{})['evaluation.karthos.dev/resource-policy']=RESOURCE_POLICY
            for container in pod.get('containers',[]):
                container['image']=pin(container['image']);scheduling_requests(container,name,mode)
            for container in pod.get('initContainers',[]):
                container['image']=pin(container['image']);scheduling_requests(container,name,mode,ordinary_init=container.get('restartPolicy')!='Always')
            if kind!='DaemonSet':
                if mode == 'karthos' or kind == 'Job':
                    slot = 'observability'
                elif 'checkout' in name or name in ('telemetry-docs','payment'):
                    # Approved payment move: app-a exceeded allocatable CPU with GKE system reservations.
                    # Keep frontend/checkout cross-node; requests and worker count are unchanged.
                    slot = 'app-b'
                elif name == 'currency':
                    # Astronomy rebalance: retain requests and checkout/frontend split.
                    # Live node admission evidence still gates deployment.
                    slot = 'app-a'
                elif 'frontend' in name:
                    slot = 'app-a'
                else:
                    slot = ['app-a', 'app-b'][int(hashlib.sha256(name.encode()).hexdigest(),16)%2]
                pod.setdefault('nodeSelector',{})['evaluation.karthos.dev/slot']=slot
        storage_class='eval-'+hashlib.sha256(run_id.encode()).hexdigest()[:16]
        if kind=='PersistentVolumeClaim':spec['storageClassName']=storage_class
        for claim in spec.get('volumeClaimTemplates',[]):
            claim.setdefault('spec',{})['storageClassName']=storage_class
            claim.setdefault('metadata',{}).setdefault('labels',{}).update(meta['labels'])
        if kind=='Service' and spec.get('type') in ('LoadBalancer','NodePort'):
            spec['type']='ClusterIP';spec.pop('externalTrafficPolicy',None);spec.pop('loadBalancerIP',None)
            for port in spec.get('ports',[]):port.pop('nodePort',None)
        out.append(d)
    return out

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--lock',required=True);p.add_argument('--run-id',required=True);p.add_argument('--mode',choices=['demo','karthos'],required=True);a=p.parse_args(argv)
    import yaml
    items=list(yaml.safe_load_all(sys.stdin));lock=json.loads(Path(a.lock).read_text());sys.stdout.write(yaml.safe_dump_all(transform(items,lock,run_id=a.run_id,mode=a.mode),sort_keys=False))
if __name__=='__main__':main()
