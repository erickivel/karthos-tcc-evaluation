"""Controller integration for a credential-free, public-API-only consumer Job."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from .models import definition_path,read, identifier
from .diagnostics.resources import labels, _image, OWNER_LABEL, RUN_LABEL

BACKEND_NAMESPACE='karthos-system'
BACKEND_SERVICE='karthos-backend'
JOB_SCRIPT='''import importlib.util,json,sys\nfrom pathlib import Path\nspec=importlib.util.spec_from_file_location("public_api_consumer","/consumer/api_only.py")\nmodule=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)\ncfg=json.loads(Path("/consumer/queries.json").read_text())\ntry:\n    client=module.APIConsumer(cfg["endpoint"],timeout=5,max_pages=20,contract=cfg.get("contract"))\n    if cfg.get("contract"):\n        result=module.thesis_answers(client,cfg["before"],cfg["after"],namespace=cfg["namespace"],service=cfg["service"],source=cfg["source"],container=cfg["container"],history_only=cfg.get("history_only",False))\n    else:\n        result=client.observe(cfg["before"],cfg["after"],queries=cfg["queries"])\n        result["answers"]=module.answer_questions(result["after"],result["metrics"],service=cfg["service"])\n    result["requested_questions"]=cfg["questions"]\n    validated=not cfg.get("contract") or result["openapi_validated"]
    result["state"]="observed" if validated and result["before"]["state"]==result["after"]["state"]=="observed" else "insufficient"\n    print(json.dumps(result,sort_keys=True,indent=0,separators=(",",":")),flush=True)\n    sys.exit(0 if result["state"]=="observed" else 2)\nexcept Exception as exc:\n    print(json.dumps({"state":"insufficient","error":str(exc)}),flush=True)\n    sys.exit(2)\n'''


def _json(ctx,*args,timeout=30):
    response=ctx.kube('get',*args,'-o','json','--ignore-not-found',check=False,timeout=timeout)
    if response.returncode: raise RuntimeError('consumer controller could not read '+str(args))
    text=response.stdout.decode() if isinstance(response.stdout,bytes) else response.stdout
    return json.loads(text) if text.strip() else None


def _timestamp(value):
    if isinstance(value,(int,float)):
        return datetime.fromtimestamp(value,timezone.utc).isoformat().replace('+00:00','Z')
    stamp=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if stamp.tzinfo is None: raise ValueError('snapshot instants need explicit timezone')
    return stamp.isoformat().replace('+00:00','Z')


def _owned(obj,owner):
    actual=(obj.get('metadata') or {}).get('labels') or {}
    return all(actual.get(k)==v for k,v in owner.items())


def _delete_owned(ctx,obj,owner,*,remaining=lambda:30):
    """Revalidate exact resource identity; server atomically checks UID on delete."""
    kind=obj['kind'].lower();meta=obj['metadata'];namespace=meta.get('namespace')
    args=[kind,meta['name']]+(['-n',namespace] if namespace else [])
    current=_json(ctx,*args,timeout=min(30,remaining()))
    if current is None: return
    if not _owned(current,owner) or current['metadata'].get('uid')!=meta.get('uid'):
        raise ValueError('consumer cleanup refused changed ownership/UID: '+kind)
    if not meta.get('uid'): raise ValueError('consumer cleanup UID is unavailable')
    group={'job':'/apis/batch/v1','networkpolicy':'/apis/networking.k8s.io/v1'}.get(kind,'/api/v1')
    plural={'job':'jobs','networkpolicy':'networkpolicies','configmap':'configmaps','namespace':'namespaces'}[kind]
    uri=group+('/namespaces/'+namespace if namespace else '')+'/'+plural+'/'+meta['name']
    options={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':meta['uid']},'propagationPolicy':'Foreground'}
    response=ctx.kube('delete','--raw',uri,'-f','-',input_text=json.dumps(options),check=False,cleanup=True,timeout=min(30,remaining()))
    if response.returncode: raise RuntimeError('consumer owned cleanup failed: '+kind)


def _job_observed(document, *, contract):
    return (all(document.get(key,{}).get('state')=='observed' for key in ('before','after'))
            and (not contract or (document.get('state')=='observed'
                 and document.get('openapi_validated') is True and not document.get('openapi_errors'))))


def capture_consumer(ctx,path,phase,*,spec=None,timeout=200,deadline=None):
    """Read window manifest start/end; export Job observations under consumer/.

    Uses ctx.root/config/run_id/kube/write only. All cluster operations belong to
    the approved controller; the consumer receives no kube credentials or SDK.
    Return state=observed/insufficient plus exact cleanup status. Existing owned
    names are rejected rather than overwritten, and foreign names are untouched.
    """
    path=Path(path);out=path/'consumer';out.mkdir(parents=True,exist_ok=True)
    manifest=read(path/'manifest.json')
    try:
        before=_timestamp(manifest['start']);after=_timestamp(manifest['end'])
        if datetime.fromisoformat(before.replace('Z','+00:00'))>datetime.fromisoformat(after.replace('Z','+00:00')):
            raise ValueError('inverted snapshot instants')
    except (KeyError,ValueError,TypeError,OverflowError) as exc:
        status={'state':'insufficient','reason':'window before/after bounds unavailable: '+str(exc),'phase':phase}
        ctx.write(out/'status.json',status);return status
    def remaining(reserve=0):
        if deadline is None:
            return 600
        seconds=deadline-time.monotonic()-reserve
        if seconds<=0:
            raise TimeoutError('API-only consumer observation or cleanup deadline reached')
        return seconds
    cleanup_reserve=65 if deadline is not None else 0
    image=ctx.config['readiness']['resource_helper_image'];_image(image)
    owner=labels(ctx.run_id)
    definitions=read(definition_path(ctx,'queries.yaml'))
    if definitions.get('api_only') is not True or definitions.get('missing_is_unknown') is not True:
        raise ValueError('frozen query contract must preserve API-only/missing-is-unknown behavior')
    metric_definitions=definitions.get('metric_queries')
    if metric_definitions is None:
        metric_definitions=[{'metric_name':row['name']} for row in read(definition_path(ctx,'metrics.yaml'))['metrics']]
    queries=[]
    for query in metric_definitions:
        query=dict(query,**{'from':before,'to':after})
        if query.get('metric_name','').startswith('servicegraph.') and manifest.get('source_id'):
            query.setdefault('source',manifest['source_id'])
        queries.append(query)
    thesis=definitions.get('schema_version')==2
    token=hashlib.sha256((ctx.run_id+'\0'+str(path.resolve())+'\0'+str(phase)).encode()).hexdigest()[:16]
    namespace='api-consumer-'+token;name='api-consumer'
    existing=_json(ctx,'namespace',namespace,timeout=min(30,remaining(cleanup_reserve)))
    if existing is not None:
        if not _owned(existing,owner): raise ValueError('consumer namespace has foreign ownership')
        raise ValueError('consumer namespace already exists; inspect previous capture before retry')
    backend=_json(ctx,'service',BACKEND_SERVICE,'-n',BACKEND_NAMESPACE,timeout=min(30,remaining(cleanup_reserve)))
    dns=_json(ctx,'service','kube-dns','-n','kube-system',timeout=min(30,remaining(cleanup_reserve)))
    if not backend or not dns: raise ValueError('explicit backend/DNS service identity unavailable')
    ports=[p for p in backend.get('spec',{}).get('ports',[]) if p.get('name')=='api']
    selector=backend.get('spec',{}).get('selector')
    if len(ports)!=1 or not selector: raise ValueError('backend API service selector/port is ambiguous')
    api=ports[0];api_port=api['port'];target_port=api.get('targetPort',api_port)
    backend_ip=str(ipaddress.ip_address(backend['spec']['clusterIP']));dns_ip=str(ipaddress.ip_address(dns['spec']['clusterIP']))
    endpoint='http://'+BACKEND_SERVICE+'.'+BACKEND_NAMESPACE+'.svc:'+str(api_port)
    code=(Path(ctx.root)/'evaluation/consumers/api_only.py').read_text()
    config={'endpoint':endpoint,'before':before,'after':after,'queries':queries,
            'service':definitions.get('service','checkoutservice'),'questions':definitions.get('questions',[])}
    if thesis:
        from .thesis import openapi_projection
        from .. import runner as r
        config.update(service=definitions['services'][spec['demo']],namespace=manifest['namespace'],
                      container=definitions['containers'][spec['demo']],history_only=phase=='history',
                      source=manifest.get('source_id'),contract=openapi_projection(r.REPO/'docs/openapi.yaml',
                            ctx.manifest['product_pins']['openapi_sha256']),queries=[])

    cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':name,'namespace':namespace,'labels':owner},
        'immutable':True,'data':{'api_only.py':code,'job.py':JOB_SCRIPT,'queries.json':json.dumps(config,sort_keys=True)}}
    backend_peer={'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':BACKEND_NAMESPACE}},'podSelector':{'matchLabels':selector}}
    dns_peer={'namespaceSelector':{'matchLabels':{'kubernetes.io/metadata.name':'kube-system'}},'podSelector':{'matchLabels':{'k8s-app':'kube-dns'}}}
    policy={'apiVersion':'networking.k8s.io/v1','kind':'NetworkPolicy','metadata':{'name':name,'namespace':namespace,'labels':owner},
        'spec':{'podSelector':{'matchLabels':owner},'policyTypes':['Ingress','Egress'],'ingress':[],
            'egress':[{'to':[backend_peer],'ports':[{'protocol':'TCP','port':target_port}]},
                      {'to':[{'ipBlock':{'cidr':backend_ip+('/32' if ':' not in backend_ip else '/128')}}],'ports':[{'protocol':'TCP','port':api_port}]},
                      {'to':[dns_peer,{'ipBlock':{'cidr':dns_ip+('/32' if ':' not in dns_ip else '/128')}}],
                       'ports':[{'protocol':'UDP','port':53},{'protocol':'TCP','port':53}]}]}}
    job={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':name,'namespace':namespace,'labels':owner},'spec':{
        'backoffLimit':0,'activeDeadlineSeconds':180,'template':{'metadata':{'labels':owner,'annotations':{'sidecar.istio.io/inject':'false'}},'spec':{
            'automountServiceAccountToken':False,'restartPolicy':'Never','enableServiceLinks':False,
            'nodeSelector':{'evaluation.karthos.dev/slot':'observability'},
            'dnsPolicy':'None','dnsConfig':{'nameservers':[dns_ip],'searches':['svc.cluster.local','cluster.local']},
            'securityContext':{'runAsNonRoot':True,'runAsUser':65532,'seccompProfile':{'type':'RuntimeDefault'}},
            'containers':[{'name':'consumer','image':image,'command':['python3','-B','/consumer/job.py'],
                'env':[{'name':'PYTHONDONTWRITEBYTECODE','value':'1'}],
                'resources':{'requests':{'cpu':'20m','memory':'32Mi'},'limits':{'cpu':'250m','memory':'256Mi'}},
                'securityContext':{'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}},
                'volumeMounts':[{'name':'code','mountPath':'/consumer','readOnly':True}]}],
            'volumes':[{'name':'code','configMap':{'name':name,'defaultMode':292}}]}}}}
    ns={'apiVersion':'v1','kind':'Namespace','metadata':{'name':namespace,'labels':owner,
        'annotations':{'sidecar.istio.io/inject':'false'}}}
    objects=[ns,policy,cm,job]
    ctx.write(out/'manifests.json',objects);ctx.write(out/'requested-queries.json',config)
    created=[];pending=None
    status={'state':'insufficient','phase':phase,'namespace':namespace,'cleanup_errors':[]}
    if thesis:
        status.update(job_completed=False,cleanup_verified=False,valid=False)
    try:
        for obj in objects:
            available=remaining(cleanup_reserve)
            if obj is job and deadline is not None:
                if available<20:
                    raise TimeoutError('insufficient consumer Job time before cleanup reserve')
                job['spec']['activeDeadlineSeconds']=min(180,max(1,int(available)-12))
                ctx.write(out/'manifests.json',objects)
            pending=obj['kind']
            response=ctx.kube('create','-f','-','-o','json',input_text=json.dumps(obj),check=False,timeout=min(30,available))
            if response.returncode: raise RuntimeError('consumer create failed: '+obj['kind'])
            actual=json.loads(response.stdout)
            meta=actual.get('metadata',{})
            if (not meta.get('uid') or actual.get('kind')!=obj['kind'] or not _owned(actual,owner)
                    or any(meta.get(k)!=obj['metadata'].get(k) for k in ('name','namespace'))):
                raise ValueError('created consumer resource identity or UID unavailable')
            created.append(actual);pending=None
        wait_budget=min(timeout,remaining(cleanup_reserve+10))
        waited=ctx.kube('wait','--for=condition=complete','job/'+name,'-n',namespace,'--timeout='+str(max(1,int(wait_budget)-(2 if thesis else 10)))+'s',check=False,timeout=wait_budget)
        if thesis and waited.returncode==0:
            actual=_json(ctx,'job',name,'-n',namespace,timeout=min(10,remaining(cleanup_reserve)))
            status['job_completed']=bool(actual and _owned(actual,owner)
                and actual.get('metadata',{}).get('uid')==created[-1]['metadata']['uid']
                and any(row.get('type')=='Complete' and row.get('status')=='True'
                        for row in actual.get('status',{}).get('conditions',[])))
        logs=ctx.kube('logs','job/'+name,'-n',namespace,'-c','consumer','--limit-bytes=33554432',check=False,
                      timeout=min(30,remaining(cleanup_reserve)))
        text=logs.stdout.decode() if isinstance(logs.stdout,bytes) else logs.stdout
        ctx.write(out/'stdout.json',text)
        if logs.returncode: raise RuntimeError('consumer Job logs unavailable')
        document=json.loads(text)
        ctx.write(out/'ledger.json',document)
        ctx.write(out/'questions.json',document.get('answers',{}))
        snapshots={key:document.get(key,{}).get('snapshot') for key in ('before','after')}
        ctx.write(out/'snapshot-ids.json',{'requested_before':before,'requested_after':after,**snapshots,
                                       'snapshot_changed':document.get('snapshot_changed')})
        observed=_job_observed(document,contract=thesis)
        if thesis:
            observed=(observed and status['job_completed']
                      and document.get('requested_questions')==config['questions']
                      and all(snapshots[key] and snapshots[key].get('id') is not None
                              and document[key].get('at')==requested
                              for key,requested in (('before',before),('after',after))))
        status.update(state='observed' if observed and waited.returncode==0 else 'insufficient',
                      wait_returncode=waited.returncode,snapshot_changed=document.get('snapshot_changed'),
                      history_claim='Only retained snapshots selected by requested at= instants; no guessed history.')
    except Exception as exc:
        status.update(state='insufficient',error=str(exc))
    finally:
        if thesis and pending is not None:
            status['cleanup_errors'].append('create may have changed '+pending+' without a verified owned UID')
        for obj in reversed(created):
            if obj['kind']=='Namespace' and status['cleanup_errors']:
                status['namespace_retained']='cleanup refused a changed child identity; retain namespace for inspection'
                continue
            try: _delete_owned(ctx,obj,owner,remaining=remaining)
            except Exception as exc: status['cleanup_errors'].append(str(exc))
        if thesis and created and not status['cleanup_errors']:
            try:
                cleanup_deadline=time.monotonic()+min(50,remaining())
                while True:
                    left=cleanup_deadline-time.monotonic()
                    if left<=0:
                        raise TimeoutError('consumer namespace removal did not verify')
                    current=_json(ctx,'namespace',namespace,timeout=min(5,left))
                    if current is None:
                        status['cleanup_verified']=True
                        break
                    if not _owned(current,owner) or current['metadata'].get('uid')!=created[0]['metadata']['uid']:
                        raise ValueError('consumer namespace identity changed during cleanup verification')
                    time.sleep(min(1,max(0,cleanup_deadline-time.monotonic())))
            except Exception as exc:
                status['cleanup_errors'].append(str(exc))
        if status['cleanup_errors'] or (thesis and not status['cleanup_verified']):
            status['state']='insufficient'
        if thesis:
            status['valid']=status['state']=='observed'
        ctx.write(out/'status.json',status)
    return status
