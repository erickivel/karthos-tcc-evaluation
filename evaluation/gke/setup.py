"""Approved GKE setup through existing discovery/chart interfaces, no product edits."""
from __future__ import annotations
import contextlib,hashlib,json,os,shlex,subprocess,time,shutil
from pathlib import Path
from .models import read,write,sha
from .render import transform

SLOT='evaluation.karthos.dev/slot'

def validate_nodes(doc):
    items=doc.get('items',[])
    if len(items)!=3:raise ValueError('exactly three worker nodes required')
    rows=[]
    for node in items:
        meta=node['metadata'];slot=meta.get('labels',{}).get(SLOT)
        if slot not in ('observability','app-a','app-b') or not meta.get('uid'):raise ValueError('node identity/slot missing')
        if not any(c['type']=='Ready' and c['status']=='True' for c in node.get('status',{}).get('conditions',[])):raise ValueError('node not Ready')
        rows.append({'name':meta['name'],'uid':meta['uid'],'slot':'infra' if slot=='observability' else slot,'node_slot':slot,'allocatable':node['status'].get('allocatable',{}),'version':node['status'].get('nodeInfo',{}).get('kubeletVersion')})
    if len({x['uid'] for x in rows})!=3 or len({x['node_slot'] for x in rows})!=3:raise ValueError('duplicate node identity or placement slot')
    return rows

def apply(ctx,objects):return ctx.kube('apply','-f','-',input_text=json.dumps({'apiVersion':'v1','kind':'List','items':objects if isinstance(objects,list) else [objects]}))

def deployment_lock(ctx):
    lock=dict(ctx.manifest['image_lock']);lock.update(ctx.config['infrastructure'].get('extra_image_lock',{}))
    for name,image in ctx.config['infrastructure'].get('product_image_mirrors',{}).items():
        original=[v for k,v in ctx.manifest['image_lock'].items() if '/'+name+':' in k]
        if not original and ctx.manifest['image_lock'].get(name+':latest')==image:
            continue
        if len(original)!=1 or image.split('@')[-1]!=original[0].split('@')[-1]:raise ValueError('product mirror must preserve validated image digest: '+name)
        for key,value in ctx.manifest['image_lock'].items():
            if '/'+name+':' in key:
                lock[key]=image;lock[value]=image
        lock[name+':latest']=image
    return lock

def post_renderer(ctx,mode):
    lock=ctx.work/'deployment-image-lock.json';write(lock,deployment_lock(ctx))
    if mode not in ('demo','karthos'):raise ValueError('unknown renderer mode')
    name='karthos-eval-'+mode
    directory=ctx.work/'helm-plugins'/name;directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
    errors=ctx.result/'infrastructure'/('helm-renderer-'+mode+'.stderr.log');errors.parent.mkdir(parents=True,exist_ok=True)
    path=directory/'render.sh';path.write_text('#!/bin/sh\nexec '+shlex.join(['python3','-B','-m','evaluation.gke.render','--lock',str(lock),'--run-id',ctx.run_id,'--mode',mode])+' 2>> '+shlex.quote(str(errors))+'\n');path.chmod(0o700)
    # Helm 4.0.4 accepts a registered postrenderer/v1 plugin name, not an executable path.
    write(directory/'plugin.yaml',{'apiVersion':'v1','name':name,'version':'1.0.0','type':'postrenderer/v1','runtime':'subprocess','runtimeConfig':{'platformCommand':[{'command':'${HELM_PLUGIN_DIR}/render.sh'}]}})
    return name

@contextlib.contextmanager
def legacy_context(ctx):
    """Single-controller adapter; all workers share one explicit immutable target."""
    from .. import runner as r
    if ctx.manifest.get('schedule')=='thesis-repair-validation-v2':
        from .models import validate_v2_inputs
        validate_v2_inputs(ctx.manifest,ctx.run_id)
    from .. import semantics_pilot as sp
    attrs=['ROOT','REPO','CONFIG','OUT','CACHE','_DEADLINE','cmd','kube','kube_json','deploy_karthos','install_demo','prepare_source','apply','reset_cell'];old={k:getattr(r,k) for k in attrs};env={k:os.environ.get(k) for k in ['KUBECONFIG','GOCACHE','GOMODCACHE','HOME','HELM_PLUGINS']}
    product=Path(os.environ.get('GKE_EVAL_PRODUCT_ROOT',ctx.manifest.get('product_snapshot') or ''))
    if not product.exists():raise ValueError('reviewed product snapshot unavailable')
    r.ROOT=ctx.root;r.REPO=product;r.OUT=ctx.result;r.CACHE=Path(os.environ.get('GKE_EVAL_CACHE_ROOT',str(ctx.root/'cache')))
    r.CONFIG=dict(ctx.manifest['baseline_config'],expected_nodes=3,study_deadline_seconds=43200,artifact_recovery_seconds=1800,artifact_recovery_deadline_policy='within reserved cloud deadline',validated_product_revision=ctx.manifest['product_pins']['karthos_revision'])
    from .models import THESIS_SCHEDULES
    r.CONFIG['thesis_metric_contract']=ctx.manifest.get('schedule') in THESIS_SCHEDULES
    if ctx.manifest.get('schedule')=='thesis-repair-validation-v2':
        for key in ('capture_finalization_version','servicegraph_store_max_items'):
            r.CONFIG[key]=ctx.config[key]
    r.CONFIG['kubelet_transport']='private-kubernetes-proxy'
    r.CONFIG['gateway_reader_cpu_limit_m'] = ctx.config['infrastructure'].get('gateway_reader_cpu_limit_m', 750)
    lock=deployment_lock(ctx)
    for key,value in list(r.CONFIG.items()):
        if key.endswith('_image') and isinstance(value,str):
            from .render import normalized
            matches={normalized(k):v for k,v in lock.items()}
            if normalized(value) in matches:r.CONFIG[key]=matches[normalized(value)]
    r._DEADLINE=time.monotonic()+max(0,ctx.deadline-time.time()-ctx.config['limits']['cleanup_reserve_seconds'])
    os.environ['HELM_PLUGINS']=str(ctx.work/'helm-plugins')
    os.environ['KUBECONFIG']=str(ctx.kubeconfig);os.environ['GOCACHE']=str(ctx.work/'gocache');os.environ['GOMODCACHE']=str(ctx.work/'gomodcache');os.environ['HOME']=str(ctx.work/'home')
    def command(args,**kw):
        if args[:2]==['helm','upgrade']:
            ns=args[args.index('-n')+1] if '-n' in args else ''
            args=[*args,'--kubeconfig',str(ctx.kubeconfig),'--post-renderer',str(post_renderer(ctx,'karthos' if ns=='karthos-system' else 'demo'))]
        if args[:3]==['git','rev-parse','HEAD'] and kw.get('cwd')==product:
            return subprocess.CompletedProcess(args,0,ctx.manifest['product_pins']['karthos_revision']+'\n','')
        capture=kw.pop('capture',None);kw.pop('quiet',None);value=ctx.command(args,**kw)
        if capture:write(capture,(value.stdout if isinstance(value.stdout,str) else value.stdout.decode(errors='backslashreplace')))
        return value
    r.cmd=command;r.kube=ctx.kube;r.kube_json=ctx.json
    def apply_legacy(obj):
        objects=obj if isinstance(obj,list) else [obj]
        mode='karthos' if all(x.get('metadata',{}).get('namespace')=='karthos-system' for x in objects) else 'demo'
        return apply(ctx,transform(objects,deployment_lock(ctx),run_id=ctx.run_id,mode=mode))
    r.apply=apply_legacy
    r.prepare_source=lambda source:prepare_source(ctx,source)
    r.deploy_karthos=lambda cell,path:deploy_karthos(ctx,cell,path)
    r.reset_cell=lambda replace_existing:old['reset_cell'](replace_existing,namespace_reset=lambda:reset_demo_namespaces(ctx))
    original_install=old['install_demo']
    r.install_demo=lambda cell,path:original_install(cell,path)
    try:yield
    finally:
        for k,v in old.items():setattr(r,k,v)
        for k,v in env.items():
            if v is None:os.environ.pop(k,None)
            else:os.environ[k]=v

def stage(ctx):
    from ..runner import tree_digest,repository_fingerprint
    product=Path(os.environ.get('GKE_EVAL_PRODUCT_ROOT',ctx.manifest.get('product_snapshot') or ''))
    pm=Path(os.environ.get('GKE_EVAL_PRODUCT_MANIFEST',str(product.parent/'product-manifest.json')))
    if pm.exists():
        m=read(pm)
        for name,digest in m['files'].items():
            p=product/name
            if not p.resolve().is_relative_to(product.resolve()) or p.is_symlink() or sha(p)!=digest:raise ValueError('packaged product source hash mismatch')
        actual=m.get('original_source_hash',m.get('source_hash'))
    else:actual=repository_fingerprint(product)
    if actual!=ctx.manifest['product_pins']['karthos_source_hash']:raise ValueError('validated product snapshot changed')
    for name,key in [('boutique','boutique_source_hash'),('opentelemetry-demo','astronomy_chart_hash'),('karthos-chart','karthos_chart_hash')]:
        p=ctx.root/'cache'/name
        if not p.is_dir() or tree_digest(p,exclude={'.git'})!=ctx.manifest['product_pins'][key]:raise ValueError('pinned chart/source cache missing or changed: '+name)
    if ctx.manifest.get('schedule')=='thesis-repair-validation-v2':
        from .quarantine import probe_access
        receipt=probe_access(ctx,ctx.work/'quarantine-probe')
        ctx.write(ctx.result/'infrastructure/quarantine-access-probe.json',receipt)

def controller_headroom(ctx,free_bytes):
    limit=ctx.config['limits']['max_evidence_bytes'];used=ctx.state.get('cumulative_raw_evidence_bytes',0)
    if not 0<=used<=limit:raise ValueError('invalid accumulated evidence byte budget')
    remaining=limit-used;required=2*remaining+20*1024**3
    result={'available_bytes':free_bytes,'required_bytes':required,'remaining_raw_budget_bytes':remaining,'reserve_bytes':20*1024**3,'reason':'retain original evidence plus sealed archives, with bounded projection/tool headroom'}
    if free_bytes<required:raise ValueError('controller disk headroom insufficient for remaining bounded evidence')
    return result

def verify_versions(ctx):
    nodes=validate_nodes(ctx.json('get','nodes'));actual=ctx.json('version')
    expected=ctx.config['infrastructure']['kubernetes_version']
    server=actual.get('serverVersion',{}).get('gitVersion','').removeprefix('v')
    if server!=expected:raise ValueError('actual GKE server version differs from frozen version')
    if any(node['version'].removeprefix('v')!=expected for node in nodes):
        raise ValueError('actual worker version differs from frozen version')
    return {'valid':True,'expected_version':expected,'server_version':server,'nodes':nodes,'observed_at':time.time()}


def preflight(ctx):
    ctx.write(ctx.result/'controller-headroom.json',controller_headroom(ctx,shutil.disk_usage(ctx.work).free))
    verified=verify_versions(ctx);nodes=verified['nodes'];write(ctx.result/'node-inventory.json',nodes)
    write(ctx.result/'kubernetes-version.json',verified)
    for node in nodes:
        summary=json.loads(ctx.kube('get','--raw','/api/v1/nodes/'+node['name']+'/proxy/stats/summary').stdout)
        if summary.get('node',{}).get('fs',{}).get('availableBytes',0)<8*1024**3:raise ValueError('insufficient node filesystem headroom')
    return nodes

def plan_command(profile,values,resolved,chart,*,thesis=False,servicegraph_store_max_items=None):
    args=['go','run','./cmd/discovery-job','--mode=plan','--capability-profile='+str(profile),
          '--values-output='+str(values),'--resolved-plan-output='+str(resolved),'--namespace=karthos-system',
          '--chart-path='+str(chart),'--storage-backend=external','--postgresql-mode=bundled','--victoriametrics-mode=bundled']
    if servicegraph_store_max_items is not None:
        if type(servicegraph_store_max_items) is not int or servicegraph_store_max_items<=0:
            raise ValueError('servicegraph_store_max_items must be a positive integer')
        args.append('--servicegraph-store-max-items='+str(servicegraph_store_max_items))
    elif not thesis:
        args.append('--servicegraph-store-max-items=10000')
    return args


def deploy_karthos(ctx,cell,path):
    from .. import runner as r
    from ..protocol import selected_source,source_id
    apply(ctx,{'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':r.MARKER,'namespace':r.KARTHOS_NAMESPACE,'labels':{r.OWNER_LABEL:r.OWNER_VALUE}},'data':{'run':ctx.run_id}})
    profile=path/'profile.json';values=path/'karthos-values.yaml';resolved=path/'resolved-plan.json'
    ctx.command(['go','run','./cmd/discovery-job','--mode=discover','--kubeconfig='+str(ctx.kubeconfig),'--cluster-name='+ctx.run_id,'--output='+str(profile),'--namespace=karthos-system','--target-namespace='+cell.namespace],cwd=r.REPO,timeout=600)
    from .models import THESIS_SCHEDULES
    ctx.command(plan_command(profile,values,resolved,r.CACHE/'karthos-chart',
                             thesis=ctx.manifest.get('schedule') in THESIS_SCHEDULES,
                             servicegraph_store_max_items=ctx.config.get('servicegraph_store_max_items') if ctx.manifest.get('schedule')=='thesis-repair-validation-v2' else None),cwd=r.REPO,timeout=600)
    if ctx.manifest.get('schedule') in THESIS_SCHEDULES:
        # The archived values contain the effective connector configuration.
        # Legacy epochs preserve defaults; v2 explicitly overrides capacity and verifies effective settings.
        write(path/'generated-collector-contract.json',{'product_revision':ctx.manifest['product_pins']['karthos_revision'],
            'connector_policy':'explicit reviewed v2 override' if ctx.manifest.get('schedule')=='thesis-repair-validation-v2' else 'product-generated defaults','values_sha256':sha(values),
            'source_plan_sha256':sha(resolved)})
    if selected_source(read(resolved))!=source_id(cell.source):raise ValueError('discovery selected wrong source')
    # Post-renderer maps the generated default product repository/tag to the frozen product digest.
    ctx.command(['helm','upgrade','--install','karthos',str(r.CACHE/'karthos-chart'),'-n','karthos-system','--create-namespace','--kubeconfig',str(ctx.kubeconfig),'-f',str(values),'--post-renderer',str(post_renderer(ctx,'karthos')),'--wait','--timeout','12m'],timeout=900)
    for resource in ['deployment/karthos-backend','daemonset/karthos-agent','deployment/karthos-gateway']:ctx.kube('rollout','status',resource,'-n','karthos-system','--timeout=300s',timeout=330)
    write(path/'locked-image-ids.json',{'demo':r.image_ids(ctx.json('get','pods','-n',cell.namespace)),'karthos':r.image_ids(ctx.json('get','pods','-n','karthos-system'))})
    if ctx.manifest.get('schedule')=='thesis-repair-validation-v2':
        verify_effective_settings(ctx,path,values,trace=cell.source=='trace')
        validate_running_images(r.image_ids(ctx.json('get','pods','-n',cell.namespace)),deployment_lock(ctx))

def prepare_source(ctx,source):
    # Existing product remote-profile scripts are intentionally not invoked on GKE.
    current=ctx.kube('get','namespace','istio-system','--ignore-not-found','-o','json')
    namespace_exists=bool(current.stdout.strip())
    if namespace_exists:
        meta=json.loads(current.stdout)['metadata'];labels=meta.get('labels',{})
        if labels.get('evaluation.karthos.dev/owner')!='gke-qualification-v1' or labels.get('evaluation.karthos.dev/run-id')!=ctx.run_id:raise ValueError('refusing unowned Istio namespace')
    if source=='trace':
        if namespace_exists:ctx.kube('delete','namespace','istio-system','--wait=true','--timeout=180s',timeout=210)
        # Cluster-scoped injection hooks must not leave broken admission after removal.
        hooks=ctx.json('get','mutatingwebhookconfigurations')
        for item in hooks.get('items',[]):
            if item.get('metadata',{}).get('labels',{}).get('evaluation.karthos.dev/run-id')==ctx.run_id:ctx.kube('delete','mutatingwebhookconfiguration',item['metadata']['name'])
        return
    import yaml
    lock=deployment_lock(ctx);proxy=lock.get('docker.io/istio/proxyv2:1.28.2')
    if not proxy:raise ValueError('reviewed Istio proxy digest absent')
    version=ctx.command(['istioctl','version','--remote=false','--short']).stdout.strip()
    if version.removeprefix('client version: ')!='1.28.2':raise ValueError('Istio toolchain version differs from frozen image contract')
    # Istio 1.28.2 takes a full reference separately for the init and sidecar.
    # An unset init override falls back to hub + proxy.image + tag.
    rendered=ctx.command(['istioctl','manifest','generate','--set','profile=minimal','--set','tag=1.28.2',
                          '--set','values.global.proxy.image='+proxy,'--set','values.global.proxy_init.image='+proxy,
                          '--set',r'values.pilot.nodeSelector.evaluation\.karthos\.dev/slot=observability']).stdout
    objects=transform(list(yaml.safe_load_all(rendered)),lock,run_id=ctx.run_id,mode='karthos')
    write(ctx.result/'infrastructure/istio-rendered.json',objects)
    if not namespace_exists:
        namespace={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'istio-system','labels':{
            'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':ctx.run_id}}}
        ctx.kube('create','-f','-',input_text=json.dumps(namespace))
    for item in objects:apply(ctx,item)
    ctx.kube('rollout','status','deployment/istiod','-n','istio-system','--timeout=180s',timeout=210)

def verify_namespace_ownership(ctx, names, *, expected_uids=None, allow_absent=False,
                               marker_required=False, expected_marker_uid=None):
    """Read-only GKE owner/run/UID check; local marker/PVC checks are separate.

    Explicit expected_uids from the active setup snapshot takes precedence over
    in-memory state. Partial setup may allow absent namespaces, but every found
    namespace still requires exact GKE owner/run labels and a live UID.
    """
    from .provision import OWNER
    expected = ctx.state.get('owned_namespace_uids', {}) if expected_uids is None else expected_uids
    result = {'safe':True, 'namespaces':{}, 'absent':[], 'observed_at':time.time()}
    for name in sorted(set(names)):
        response = ctx.kube('get','namespace',name,'--ignore-not-found','-o','json',check=False,cleanup=True,timeout=15)
        if response.returncode:
            raise ValueError('cannot verify GKE namespace ownership: '+name)
        if not response.stdout.strip():
            if allow_absent and name not in expected:
                result['absent'].append(name)
                continue
            raise ValueError('expected GKE namespace missing: '+name)
        document=json.loads(response.stdout);meta=document.get('metadata',{})
        labels=meta.get('labels',{})
        if (document.get('kind')!='Namespace' or meta.get('name')!=name or not meta.get('uid')
                or meta.get('deletionTimestamp')
                or labels.get('evaluation.karthos.dev/owner')!=OWNER
                or labels.get('evaluation.karthos.dev/run-id')!=ctx.run_id):
            raise ValueError('GKE namespace ownership, run or live identity differs: '+name)
        if name in expected and expected[name]!=meta['uid']:
            raise ValueError('GKE namespace UID changed: '+name)
        result['namespaces'][name]={'uid':meta['uid'], 'owner':OWNER, 'run_id':ctx.run_id,
                                  'uid_basis':'setup-snapshot' if name in expected else 'observed-only'}
    from .. import runner as r
    if r.KARTHOS_NAMESPACE in result['namespaces']:
        response=ctx.kube('get','configmap',r.MARKER,'-n',r.KARTHOS_NAMESPACE,
                          '--ignore-not-found','-o','json',check=False,cleanup=True,timeout=15)
        if response.returncode:raise ValueError('cannot verify Karthos ownership marker')
        if not response.stdout.strip():
            if marker_required or expected_marker_uid:raise ValueError('expected Karthos ownership marker missing')
            result['marker']={'state':'absent','required':False}
        else:
            document=json.loads(response.stdout);meta=document.get('metadata',{})
            if (document.get('kind')!='ConfigMap' or meta.get('name')!=r.MARKER
                    or meta.get('namespace')!=r.KARTHOS_NAMESPACE or not meta.get('uid')
                    or meta.get('deletionTimestamp')
                    or meta.get('labels',{}).get(r.OWNER_LABEL)!=r.OWNER_VALUE
                    or document.get('data',{}).get('run')!=ctx.run_id):
                raise ValueError('Karthos ownership marker labels, run or identity differs')
            if expected_marker_uid and meta['uid']!=expected_marker_uid:
                raise ValueError('Karthos ownership marker UID changed')
            result['marker']={'state':'verified','uid':meta['uid'],'run_id':ctx.run_id,
                              'uid_basis':'setup-snapshot' if expected_marker_uid else 'observed-only'}
    return result


def record_namespace_ownership(ctx, cell, path):
    """Retain active successful setup identities, excluding stale prior cells."""
    from .. import runner as r
    names=[r.KARTHOS_NAMESPACE,cell.namespace]
    if cell.source=='mesh':names.append('istio-system')
    result=verify_namespace_ownership(ctx,names,expected_uids={},marker_required=True)
    uids={name:row['uid'] for name,row in result['namespaces'].items()}
    result.update(namespace_uids=uids, basis='successful-cell-setup')
    ctx.write(Path(path)/'namespace-ownership.json',result)
    ctx.state['owned_namespace_uids']=uids
    ctx.state['owned_marker_uid']=result['marker']['uid']
    return result


def ensure_owned_namespaces(ctx):
    from .. import runner as r
    for name in [r.KARTHOS_NAMESPACE,*r.NAMESPACES.values()]:
        result=ctx.kube('get','namespace',name,'-o','json',check=False)
        if result.returncode==0:
            labels=json.loads(result.stdout)['metadata'].get('labels',{})
            if labels.get('evaluation.karthos.dev/owner')!='gke-qualification-v1' or labels.get('evaluation.karthos.dev/run-id')!=ctx.run_id:raise ValueError('refusing existing unowned namespace '+name)
    if ctx.kube('get','namespace',r.KARTHOS_NAMESPACE,check=False).returncode!=0:
        apply(ctx,{'apiVersion':'v1','kind':'Namespace','metadata':{'name':r.KARTHOS_NAMESPACE,'labels':{'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':ctx.run_id}}})

def reset_demo_namespaces(ctx):
    from .run import _owned_delete
    from .provision import OWNER
    from .. import runner as r
    for name in r.NAMESPACES.values():
        _owned_delete(ctx,'Namespace',name,None,OWNER,ctx.run_id,cluster=True)


def setup_cell(ctx,cell,parent):
    from .. import runner as r,pilot
    path=Path(parent)/cell.slug;path.mkdir(parents=True,exist_ok=True)
    ensure_owned_namespaces(ctx)
    sc='eval-'+hashlib.sha256(ctx.run_id.encode()).hexdigest()[:16]
    apply(ctx,{'apiVersion':'storage.k8s.io/v1','kind':'StorageClass','metadata':{'name':sc,'labels':{'evaluation.karthos.dev/owner':'gke-qualification-v1','evaluation.karthos.dev/run-id':ctx.run_id}},'provisioner':'pd.csi.storage.gke.io','reclaimPolicy':'Delete','volumeBindingMode':'WaitForFirstConsumer','parameters':{'type':'pd-balanced','labels':'evaluation-owner=gke-qualification-v1,evaluation-run='+ctx.run_id}})
    r.reset_cell(False);r.prepare_source(cell.source);r.create_namespace(cell);r.install_demo(cell,path);pilot.install_observers(cell,path,include_agent=False,setup_no_surge=True);deploy_karthos(ctx,cell,path);pilot.install_scripts(cell)
    # The independent consumer and collectors must not be silently co-located with every app.
    pods=ctx.json('get','pods','-n',cell.namespace)['items'];nodes={n['name']:n['node_slot'] for n in validate_nodes(ctx.json('get','nodes'))}
    for term,slot in [('frontend','app-a'),('checkout','app-b')]:
        found=[p for p in pods if term in p['metadata']['name']]
        if not found or any(nodes.get(p.get('spec',{}).get('nodeName'))!=slot for p in found):raise ValueError('cross-node application placement missing: '+term)
    write(path/'placement.json',{'nodes':nodes,'frontend_slot':'app-a','checkout_slot':'app-b'})
    record_namespace_ownership(ctx,cell,path)
    return path


def _duration_seconds(value):
    import re
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(ms|s)',str(value))
    if not match:raise ValueError('unsupported connector duration: '+str(value))
    return float(match[1])/(1000 if match[2]=='ms' else 1)


def validate_connector_settings(document,expected):
    """Verify semantic bounds and explicit effective settings; never repair config."""
    connector=document.get('connectors',{}).get('service_graph',{})
    store=connector.get('store',{})
    bounds=[_duration_seconds(x) for x in connector.get('latency_histogram_buckets',[])]
    if (type(expected) is not int or expected<=0 or type(store.get('max_items')) is not int
            or store.get('max_items')!=expected or _duration_seconds(store.get('ttl'))!=30
            or _duration_seconds(connector.get('metrics_flush_interval'))!=15
            or bounds!=[.1,.25,.5,1.,5.]):
        raise ValueError('effective servicegraph capacity/TTL/flush/histogram contract mismatch')
    return {'store_max_items':expected,'store_ttl_seconds':30,'flush_seconds':15,'histogram_bounds_seconds':bounds}


def validate_running_images(rows,lock):
    import re
    allowed=set(lock.values())
    if not rows:raise ValueError('running image inventory missing')
    for row in rows:
        image=row.get('image','');runtime=row.get('image_id','')
        digest=re.search(r'sha256:[a-f0-9]{64}$',runtime)
        if image not in allowed or '@sha256:' not in image or not digest or image.rsplit('@',1)[1]!=digest[0]:
            raise ValueError('running image differs from immutable deployment lock: '+str(row.get('container')))
    return {'verified_images':len(rows)}


def verify_effective_settings(ctx,path,values,*,trace):
    """Read generated values, live mounted file and running digests before traffic."""
    import yaml
    from .. import runner as r
    intended={'capture_finalization_version':2,'servicegraph_store_max_items':10000}
    if any(ctx.config.get(k)!=v or ctx.manifest.get(k)!=v for k,v in intended.items()):
        raise ValueError('v2 intended active settings differ')
    generated=yaml.safe_load(Path(values).read_text())
    pods=ctx.json('get','pods','-n','karthos-system')
    result={'intended':intended,'values_sha256':sha(values),'checked_before_traffic':True}
    if trace:
        text=generated.get('gateway',{}).get('collectorConfig')
        if not isinstance(text,str):raise ValueError('generated gateway collector config missing')
        result['generated']=validate_connector_settings(yaml.safe_load(text),10000)
        gateways=[p for p in pods.get('items',[]) if p.get('metadata',{}).get('labels',{}).get('app.kubernetes.io/component')=='gateway']
        if not gateways:raise ValueError('running gateway inventory missing')
        result['mounted_file_proof']={'state':'pending','required_before_traffic':True,
            'owner':'RepairCapture.start v2; same-pod diag-reader reads original config volume'}
        result['desired_configmaps']=[]
        for pod in gateways:
            containers=pod.get('spec',{}).get('containers',[])
            mounts=[m for c in containers for m in c.get('volumeMounts',[]) if m.get('mountPath')=='/conf']
            if len(mounts)!=1:raise ValueError('unique gateway config mount missing')
            volumes=[v for v in pod['spec'].get('volumes',[]) if v['name']==mounts[0]['name'] and v.get('configMap',{}).get('name')]
            if len(volumes)!=1:raise ValueError('gateway configMap volume missing')
            name=volumes[0]['configMap']['name']
            cm=ctx.json('get','configmap',name,'-n','karthos-system')
            desired=cm.get('data',{}).get('config.yaml')
            parsed=yaml.safe_load(desired) if desired else None
            if parsed!=yaml.safe_load(text):raise ValueError('live collector ConfigMap differs from generated values')
            contract=validate_connector_settings(parsed,10000)
            result['desired_configmaps'].append(dict(contract,pod_uid=pod['metadata']['uid'],
                configmap_name=name,configmap_uid=cm['metadata']['uid'],config_key='config.yaml',
                config_sha256=hashlib.sha256(desired.encode()).hexdigest()))
    rows=r.image_ids(pods)
    result['running_images']=validate_running_images(rows,deployment_lock(ctx))
    write(path/'effective-settings.json',result)
    return result
