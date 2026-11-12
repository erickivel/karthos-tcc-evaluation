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
    from .. import semantics_pilot as sp
    attrs=['ROOT','REPO','CONFIG','OUT','CACHE','_DEADLINE','cmd','kube','kube_json','deploy_karthos','install_demo','prepare_source','apply','reset_cell'];old={k:getattr(r,k) for k in attrs};env={k:os.environ.get(k) for k in ['KUBECONFIG','GOCACHE','GOMODCACHE','HOME','HELM_PLUGINS']}
    product=Path(os.environ.get('GKE_EVAL_PRODUCT_ROOT',ctx.manifest.get('product_snapshot') or ''))
    if not product.exists():raise ValueError('reviewed product snapshot unavailable')
    r.ROOT=ctx.root;r.REPO=product;r.OUT=ctx.result;r.CACHE=Path(os.environ.get('GKE_EVAL_CACHE_ROOT',str(ctx.root/'cache')))
    r.CONFIG=dict(ctx.manifest['baseline_config'],expected_nodes=3,study_deadline_seconds=43200,artifact_recovery_seconds=1800,artifact_recovery_deadline_policy='within reserved cloud deadline',validated_product_revision=ctx.manifest['product_pins']['karthos_revision'])
    from .models import THESIS_SCHEDULES
    r.CONFIG['thesis_metric_contract']=ctx.manifest.get('schedule') in THESIS_SCHEDULES
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

def plan_command(profile,values,resolved,chart,*,thesis=False):
    args=['go','run','./cmd/discovery-job','--mode=plan','--capability-profile='+str(profile),
          '--values-output='+str(values),'--resolved-plan-output='+str(resolved),'--namespace=karthos-system',
          '--chart-path='+str(chart),'--storage-backend=external','--postgresql-mode=bundled','--victoriametrics-mode=bundled']
    if not thesis:
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
                             thesis=ctx.manifest.get('schedule') in THESIS_SCHEDULES),cwd=r.REPO,timeout=600)
    if ctx.manifest.get('schedule') in THESIS_SCHEDULES:
        # The archived values contain the effective connector configuration.
        # The audited revision declares capacity 1000, TTL 30s and flush 15s.
        write(path/'generated-collector-contract.json',{'product_revision':ctx.manifest['product_pins']['karthos_revision'],
            'connector_policy':'product-generated defaults','values_sha256':sha(values),
            'source_plan_sha256':sha(resolved)})
    if selected_source(read(resolved))!=source_id(cell.source):raise ValueError('discovery selected wrong source')
    # Post-renderer maps the generated default product repository/tag to the frozen product digest.
    ctx.command(['helm','upgrade','--install','karthos',str(r.CACHE/'karthos-chart'),'-n','karthos-system','--create-namespace','--kubeconfig',str(ctx.kubeconfig),'-f',str(values),'--post-renderer',str(post_renderer(ctx,'karthos')),'--wait','--timeout','12m'],timeout=900)
    for resource in ['deployment/karthos-backend','daemonset/karthos-agent','deployment/karthos-gateway']:ctx.kube('rollout','status',resource,'-n','karthos-system','--timeout=300s',timeout=330)
    write(path/'locked-image-ids.json',{'demo':r.image_ids(ctx.json('get','pods','-n',cell.namespace)),'karthos':r.image_ids(ctx.json('get','pods','-n','karthos-system'))})

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
    return path
