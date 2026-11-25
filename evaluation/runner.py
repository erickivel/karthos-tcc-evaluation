"""Reproducible Kivel k3s study runner. No writes to the Karthos checkout."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import evaluate_bundle, extract_marked
from .manifests import OWNER_LABEL, OWNER_VALUE, demo_values, k6_job, locust_job
from .protocol import Cell, KARTHOS_NAMESPACE, KARTHOS_RELEASE, NAMESPACES, cells_for_repeat, selected_source, source_id
from .relay import boutique_relay

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get('KARTHOS_REPO', str(Path(__file__).resolve().parents[2] / 'karthos')))
CONFIG = json.loads((ROOT / 'evaluation/config.json').read_text())
OUT = ROOT / 'results'
CACHE = ROOT / 'cache'
MARKER = 'karthos-evaluation-owner'
_DEADLINE: float | None = None


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n' if not isinstance(value, str) else value)


def cmd(args: list[str], *, cwd: Path | None = None, input_text: str | None = None,
        capture: Path | None = None, timeout: int = 900, check: bool = True, quiet: bool = False, binary: bool = False) -> subprocess.CompletedProcess:
    if not quiet:
        print('+', ' '.join(args[:4]), flush=True)
    if _DEADLINE is not None:
        remaining=_DEADLINE-time.monotonic()
        if remaining<=0:
            raise TimeoutError('eight-hour study deadline reached')
        timeout=min(timeout,remaining)
    result = subprocess.run(args, cwd=cwd, input=input_text.encode() if binary and input_text is not None else input_text, text=not binary, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    display = lambda value: value.decode('utf-8', errors='backslashreplace') if isinstance(value, bytes) else value
    if capture:
        write(capture, display(result.stdout) + ('\nSTDERR:\n' + display(result.stderr) if result.stderr else ''))
    if check and result.returncode:
        raise RuntimeError(f'{args[0]} failed ({result.returncode}): {display(result.stderr)[-1500:]}')
    return result


def kube(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return cmd(['kubectl', *args], **kwargs)


def kube_json(*args: str) -> dict:
    return json.loads(kube(*args, '-o', 'json').stdout)


def apply(objects: dict | list[dict]) -> None:
    docs = objects if isinstance(objects, list) else [objects]
    kube('apply', '-f', '-', input_text=json.dumps({'apiVersion':'v1','kind':'List','items':docs}))


def ownership() -> bool:
    result = kube('get', 'configmap', MARKER, '-n', KARTHOS_NAMESPACE, check=False)
    return result.returncode == 0


def release_exists() -> bool:
    result = cmd(['helm','status',KARTHOS_RELEASE,'-n',KARTHOS_NAMESPACE],check=False)
    return result.returncode == 0


def check_ownership(replace_existing: bool, *, check_namespaces=True) -> None:
    if release_exists() and not ownership() and not replace_existing:
        raise RuntimeError('existing Karthos Helm release is not evaluation-owned; rerun with --replace-existing only if replacing it is intended')
    if not ownership() and not replace_existing:
        for pvc in kube_json('get','pvc','-n',KARTHOS_NAMESPACE).get('items',[]):
            name=pvc['metadata']['name']
            if name.startswith(('data-karthos-postgresql-', 'server-volume-karthos-victoria')):
                raise RuntimeError(f'unowned Karthos PVC {name} would block clean storage; use --replace-existing only if its removal is intended')
    if not check_namespaces:
        return
    for ns in NAMESPACES.values():
        result = kube('get','namespace',ns,check=False)
        if result.returncode == 0:
            data = kube_json('get','namespace',ns)
            if data.get('metadata',{}).get('labels',{}).get(OWNER_LABEL) != OWNER_VALUE:
                raise RuntimeError(f'namespace {ns} exists but is not evaluation-owned')


def preflight(replace_existing: bool = False) -> dict:
    required = ['kubectl','helm','go','git','istioctl','docker']
    missing = [x for x in required if not shutil.which(x)]
    if missing:
        raise RuntimeError(f'missing tools: {missing}')
    if not os.environ.get('KUBECONFIG'):
        raise RuntimeError('KUBECONFIG is unset; use ./eval.sh so the Karthos cluster profile is loaded')
    nodes = kube_json('get','nodes')['items']
    ready = [n for n in nodes if any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions'])]
    if len(ready) != 1:
        raise RuntimeError(f'expected exactly one Ready Kivel node, got {len(ready)}')
    node = ready[0]
    summary = json.loads(kube('get','--raw',f"/api/v1/nodes/{node['metadata']['name']}/proxy/stats/summary").stdout)
    disk = summary.get('node',{}).get('fs',{}).get('availableBytes')
    if disk is None or disk < 8 * 1024**3:
        raise RuntimeError(f'need at least 8 GiB free node filesystem, found {disk}')
    memory = node['status']['allocatable']['memory']
    client_version = json.loads(cmd(['kubectl', 'version', '--client', '-o', 'json']).stdout)
    lease=kube_json('get','lease',node['metadata']['name'],'-n','kube-node-lease')
    renewed=datetime.fromisoformat(lease['spec']['renewTime'].replace('Z','+00:00'))
    lease_age=(datetime.now(timezone.utc)-renewed).total_seconds()
    if abs(lease_age)>60:
        raise RuntimeError(f'node heartbeat is {lease_age:.0f}s from local clock; check time synchronization')
    check_ownership(replace_existing)
    return {'checked_at':utc(),'node':node['metadata']['name'],'allocatable_memory':memory,
            'kubectl_client': client_version,
            'available_filesystem_bytes':disk,'node_lease_age_seconds':lease_age,
            'kubeconfig':os.environ['KUBECONFIG'],
            'existing_release':release_exists(),'owned_release':ownership()}


def stage() -> dict:
    CACHE.mkdir(exist_ok=True)
    boutique = CACHE / 'boutique'
    if not boutique.exists():
        cmd(['git','clone','--depth','1','--branch',CONFIG['boutique_git_tag'],
             'https://github.com/GoogleCloudPlatform/microservices-demo.git',str(boutique)],timeout=1200)
    actual = cmd(['git','rev-parse','HEAD'],cwd=boutique).stdout.strip()
    if actual != CONFIG['boutique_git_commit']:
        raise RuntimeError(f'Boutique tag moved: {actual}')
    astronomy = CACHE / 'opentelemetry-demo'
    # Re-fetch the published version and hash its content instead of trusting
    # an arbitrarily edited cache directory bearing the right version name.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='chart-',dir=CACHE) as temporary:
        cmd(['helm','pull','opentelemetry-demo','--repo','https://open-telemetry.github.io/opentelemetry-helm-charts',
             '--version',CONFIG['astronomy_chart_version'],'--untar','--untardir',temporary],timeout=1200)
        if astronomy.exists(): shutil.rmtree(astronomy)
        shutil.copytree(Path(temporary)/'opentelemetry-demo',astronomy)
    chart = CACHE / 'karthos-chart'
    # A cached chart must never silently deploy an older checkout's templates.
    if chart.exists():
        shutil.rmtree(chart)
    shutil.copytree(REPO / 'deploy/helm/karthos', chart)
    for path in [astronomy, chart]:
        if not (path / 'charts').exists() or not any((path / 'charts').iterdir()):
            cmd(['helm','dependency','build',str(path)],timeout=1200)
    return {'boutique_commit':actual,'astronomy_chart':CONFIG['astronomy_chart_version'],
            'boutique_source_hash':tree_digest(boutique,exclude={'.git'}),
            'astronomy_chart_hash':tree_digest(astronomy), 'karthos_chart_hash':tree_digest(chart),
            'karthos_revision':cmd(['git','rev-parse','HEAD'],cwd=REPO).stdout.strip(),
            'karthos_source_hash':repository_fingerprint(REPO),
            'karthos_dirty':bool(cmd(['git','status','--porcelain'],cwd=REPO).stdout.strip())}


def tree_digest(root: Path, exclude: set[str] | None = None) -> str:
    digest=hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if set(path.relative_to(root).parts) & (exclude or set()): continue
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode()+b'\0'+path.read_bytes()+b'\0')
    return digest.hexdigest()


def repository_fingerprint(root: Path) -> str:
    listing=cmd(['git','ls-files','-z','--cached','--others','--exclude-standard'],cwd=root,quiet=True).stdout
    digest=hashlib.sha256()
    for name in sorted(set(listing.split('\0'))-{''}):
        path=root/name
        digest.update(name.encode()+b'\0')
        digest.update(path.read_bytes() if path.is_file() else b'<missing>')
        digest.update(b'\0')
    return digest.hexdigest()


def reset_cell(replace_existing: bool, *, namespace_reset=None) -> None:
    check_ownership(replace_existing, check_namespaces=namespace_reset is None)
    owned_before=ownership()
    if namespace_reset is None:
        for ns in NAMESPACES.values():
            if kube('get','namespace',ns,check=False).returncode == 0:
                kube('delete','namespace',ns,'--wait=true','--timeout=180s')
    else:
        namespace_reset()
    if release_exists():
        cmd(['helm','uninstall',KARTHOS_RELEASE,'-n',KARTHOS_NAMESPACE])
    # StatefulSet-created PVCs are retained by Helm. Delete only exact release
    # PVCs in the Karthos namespace, after explicit release ownership check.
    pvcs = kube_json('get','pvc','-n',KARTHOS_NAMESPACE).get('items',[])
    for pvc in pvcs:
        name = pvc['metadata']['name']
        if name.startswith(('data-karthos-postgresql-', 'server-volume-karthos-victoria')):
            if not (owned_before or replace_existing):
                raise RuntimeError(f'found unowned Karthos PVC {name}; use --replace-existing if its removal is intended')
            kube('delete','pvc',name,'-n',KARTHOS_NAMESPACE,'--wait=true','--timeout=120s')


def prepare_source(source: str) -> None:
    script = REPO / 'test/e2e/cluster/istio.sh'
    if source == 'trace':
        cmd([str(script),'reset-if-owned'],cwd=REPO,timeout=600)
        if kube('get','namespace','istio-system',check=False).returncode == 0:
            raise RuntimeError('Istio remains installed and is not harness-owned; trace fallback cannot be isolated')
    else:
        cmd([str(script),'install'],cwd=REPO,timeout=600)


def image_ids(pods: dict) -> list[dict]:
    rows=[]
    for pod in pods.get('items',[]):
        statuses=(pod.get('status',{}).get('containerStatuses',[])
                  +pod.get('status',{}).get('initContainerStatuses',[]))
        for status in statuses:
            rows.append({'pod':pod['metadata']['name'],'container':status['name'],
                         'image':status.get('image'),'image_id':status.get('imageID')})
    return sorted(rows,key=lambda r:(r['pod'],r['container']))


def has_istio_proxy(pod: dict) -> bool:
    spec=pod.get('spec',{})
    return any(c.get('name')=='istio-proxy' for c in spec.get('containers',[])+spec.get('initContainers',[]))


def create_namespace(cell: Cell) -> None:
    apply({'apiVersion':'v1','kind':'Namespace','metadata':{'name':cell.namespace,'labels':{
        OWNER_LABEL:OWNER_VALUE,'istio-injection':'enabled' if cell.source=='mesh' else 'disabled'}}})
    if cell.source == 'mesh':
        kube('wait','--for=condition=available','deployment/istiod','-n','istio-system','--timeout=180s')


def install_demo(cell: Cell, cell_dir: Path) -> None:
    values = demo_values(cell.demo,protocol_version=CONFIG.get('protocol_version',2))
    values_path = cell_dir / 'demo-values.json'
    write(values_path, values)
    chart = CACHE / ('boutique/helm-chart' if cell.demo == 'boutique' else 'opentelemetry-demo')
    cmd(['helm','upgrade','--install',f'eval-{cell.demo}',str(chart),'-n',cell.namespace,
         '-f',str(values_path),'--wait','--timeout','12m'],capture=cell_dir/'demo-helm.log',timeout=900)
    if cell.demo == 'boutique':
        apply(boutique_relay(cell.namespace,CONFIG['collector_image'],self_metrics=CONFIG.get('protocol_version',2)>=4))
        kube('rollout','status','deployment/eval-otlp-relay','-n',cell.namespace,'--timeout=180s')
        # Upstream chart gates COLLECTOR_SERVICE_ADDR on its own collector.
        # Set the equivalent variable on the evaluation deployment after install.
        for deployment in kube_json('get','deployments','-n',cell.namespace).get('items',[]):
            name=deployment['metadata']['name']
            if name=='eval-otlp-relay':
                continue
            kube('set','env',f'deployment/{name}','-n',cell.namespace,
                 'COLLECTOR_SERVICE_ADDR=opentelemetrycollector:4317',
                 'ENABLE_TRACING=1',f'OTEL_SERVICE_NAME={name}')
    deployments = kube_json('get','deployments','-n',cell.namespace).get('items',[])
    for deployment in deployments:
        name=deployment['metadata']['name']
        kube('rollout','status',f'deployment/{name}','-n',cell.namespace,'--timeout=360s')
    write(cell_dir/'demo-deployments.json',kube_json('get','deployments','-n',cell.namespace))


def deploy_karthos(cell: Cell, cell_dir: Path) -> None:
    apply({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':MARKER,'namespace':KARTHOS_NAMESPACE,
          'labels':{OWNER_LABEL:OWNER_VALUE}},'data':{'run':cell.slug}})
    profile=cell_dir/'profile.json'
    values=cell_dir/'karthos-values.yaml'
    resolved=cell_dir/'resolved-plan.json'
    cmd(['go','run','./cmd/discovery-job','--mode=discover',f"--kubeconfig={os.environ['KUBECONFIG']}",
         f"--cluster-name={os.environ.get('CLUSTER_PROFILE','kivel')}",f'--output={profile}',
         f'--namespace={KARTHOS_NAMESPACE}',f'--target-namespace={cell.namespace}'],
        cwd=REPO,capture=cell_dir/'discover.log',timeout=600)
    tag=os.environ.get('IMAGE_TAG','latest')
    registry=os.environ.get('REGISTRY_HOST','localhost:5001')
    cmd(['go','run','./cmd/discovery-job','--mode=deploy',f'--capability-profile={profile}',
         f'--values-output={values}',f'--resolved-plan-output={resolved}',
         f'--rendered-output={cell_dir / "rendered"}',f'--namespace={KARTHOS_NAMESPACE}',
         f'--chart-path={CACHE / "karthos-chart"}',f'--release-name={KARTHOS_RELEASE}',
         f'--backend-image-repository={registry}/karthos-backend',f'--backend-image-tag={tag}',
         '--backend-image-pull-policy=Always',f'--frontend-image-repository={registry}/karthos-frontend',
         f'--frontend-image-tag={tag}','--frontend-image-pull-policy=Always',
         '--storage-backend=external','--postgresql-mode=bundled','--victoriametrics-mode=bundled']+
        ([f"--servicegraph-store-max-items={CONFIG['servicegraph_store_max_items']}"] if 'servicegraph_store_max_items' in CONFIG else []),
        cwd=REPO,capture=cell_dir/'deploy.log',timeout=1200)
    plan=json.loads(resolved.read_text())
    if selected_source(plan) != source_id(cell.source):
        raise RuntimeError(f"wrong source selected: {selected_source(plan)} != {source_id(cell.source)}")
    kube('rollout','status','deployment/karthos-backend','-n',KARTHOS_NAMESPACE,'--timeout=300s')
    kube('rollout','status','daemonset/karthos-agent','-n',KARTHOS_NAMESPACE,'--timeout=300s')
    write(cell_dir/'locked-image-ids.json',{'demo':image_ids(kube_json('get','pods','-n',cell.namespace)),
         'karthos':image_ids(kube_json('get','pods','-n',KARTHOS_NAMESPACE))})


@contextlib.contextmanager
def forward(resource: str, namespace: str, local: int, remote: int):
    process=subprocess.Popen(['kubectl','port-forward','-n',namespace,resource,f'{local}:{remote}'],
                             stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        for _ in range(40):
            if _DEADLINE is not None and time.monotonic() >= _DEADLINE:
                raise TimeoutError('deadline reached opening port-forward')
            if process.poll() is not None:
                raise RuntimeError(f'port-forward exited: {process.stderr.read().decode()[-800:]}')
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{local}/',timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(.25)
        else:
            raise RuntimeError(f'port-forward timeout: {resource}')
        yield f'http://127.0.0.1:{local}'
    finally:
        process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: process.kill()


def http_timeout(default: float = 30) -> float:
    remaining=default if _DEADLINE is None else min(default,_DEADLINE-time.monotonic())
    if remaining<=0: raise TimeoutError('study/export deadline reached')
    return remaining


def get_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url,timeout=http_timeout()) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        with exc:
            body = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'HTTP {exc.code}: {body}') from exc


def api_pages(base: str, route: str, params: dict, key: str, pages_dir: Path | None = None) -> dict:
    all_items=[]
    token=None
    seen=set()
    for _ in range(1000):
        query={**params,'page_size':'1000'}
        if token: query['next_token']=token
        page=get_json(base+route+'?'+urllib.parse.urlencode(query))
        if pages_dir:
            write(pages_dir/f'page-{len(seen)+1:04d}.json', {'request':query,'response':page})
        all_items.extend(page.get('data',{}).get(key,[]))
        token=page.get('next_token')
        if not token:
            return {'data':{key:all_items,'pages':len(seen)+1,'query':page.get('data',{}).get('query'),
                            'snapshot':page.get('data',{}).get('snapshot')}}
        if token in seen: raise RuntimeError(f'repeated API pagination token for {route}')
        seen.add(token)
    raise RuntimeError(f'pagination exceeded limit for {route}')


def capture(cell: Cell, cell_dir: Path, start: str, end: str) -> None:
    ns=cell.namespace
    for name,args in {'pods':['get','pods','-n',ns], 'services':['get','services','-n',ns],
                      'events':['get','events','-n',ns], 'nodes':['get','nodes']}.items():
        write(cell_dir/f'k8s-{name}.json',kube_json(*args))
    nodes=kube_json('get','nodes')['items']
    for node in nodes:
        name=node['metadata']['name']
        write(cell_dir/f'kubelet-{name}.json',json.loads(kube('get','--raw',f'/api/v1/nodes/{name}/proxy/stats/summary').stdout))
    if cell.demo=='boutique':
        write(cell_dir/'source-traces.log',kube('logs','-n',ns,'deployment/eval-otlp-relay','-c','collector',
                                               '--since=15m',check=False).stdout)
        selected=['frontend','checkoutservice','cartservice','productcatalogservice']
    else:
        write(cell_dir/'source-traces.log',kube('logs','-n',ns,'daemonset/otel-collector-agent','--since=15m',check=False).stdout)
        selected=['frontend-proxy','frontend','checkout','cart','product-catalog']
    for name in selected:
        result=kube('logs','-n',ns,f'deployment/{name}','--since=15m',check=False)
        write(cell_dir/'service-logs'/f'{name}.log',result.stdout if result.returncode==0 else result.stderr)
    write(cell_dir/'karthos-pods.json',kube_json('get','pods','-n',KARTHOS_NAMESPACE))
    write(cell_dir/'karthos-events.json',kube_json('get','events','-n',KARTHOS_NAMESPACE))
    for name,resource in [('agent','daemonset/karthos-agent'),('gateway','deployment/karthos-gateway')]:
        result=kube('logs','-n',KARTHOS_NAMESPACE,resource,'--since=15m',check=False)
        write(cell_dir/'karthos-logs'/f'{name}.log',result.stdout if result.returncode==0 else result.stderr)
    if cell.source=='mesh':
        pods=kube_json('get','pods','-n',ns).get('items',[])
        for pod in pods:
            p=pod['metadata']['name']
            if has_istio_proxy(pod):
                result=kube('get','--raw',f'/api/v1/namespaces/{ns}/pods/{p}:15090/proxy/stats/prometheus',check=False)
                write(cell_dir/'istio'/f'{p}.prom',result.stdout if result.returncode==0 else result.stderr)
    with forward('deployment/karthos-backend',KARTHOS_NAMESPACE,18081,8080) as base:
        for filename,route,params,key in [
            ('api-nodes.json','/api/v1/graph/nodes',{},'nodes'),
            ('api-edges.json','/api/v1/graph/edges',{},'edges'),
            ('api-request-raw.json','/api/v1/metrics',{'metric_name':'servicegraph.request.count',
                'from':start,'to':end,'aggregation':'latest'},'samples'),
            ('api-request-count.json','/api/v1/metrics',{'metric_name':'servicegraph.request.count',
                'from':end,'to':end,'aggregation':'increase','window':CONFIG['scoring_window']},'samples'),
            ('api-error-count.json','/api/v1/metrics',{'metric_name':'servicegraph.request.error.count',
                'from':end,'to':end,'aggregation':'increase','window':CONFIG['scoring_window']},'samples'),
            ('api-duration.json','/api/v1/metrics',{'metric_name':'servicegraph.request.duration',
                'from':start,'to':end},'samples'),
            ('api-cpu.json','/api/v1/metrics',{'metric_name':'k8s.container.cpu.usage',
                'from':start,'to':end},'samples'),
            ('api-memory.json','/api/v1/metrics',{'metric_name':'k8s.container.memory.usage',
                'from':start,'to':end},'samples')]:
            try: write(cell_dir/filename,api_pages(base,route,params,key))
            except Exception as exc: write(cell_dir/(filename+'.error'),str(exc))
    services=kube_json('get','services','-n',KARTHOS_NAMESPACE).get('items',[])
    vm=next((s['metadata']['name'] for s in services if 'victoria' in s['metadata']['name']),None)
    if vm:
        with forward(f'service/{vm}',KARTHOS_NAMESPACE,18428,8428) as base:
            params=urllib.parse.urlencode({'query':'servicegraph_request_count','start':start,'end':end,'step':'15s'})
            try: write(cell_dir/'victoriametrics-request-count.json',get_json(base+'/api/v1/query_range?'+params))
            except Exception as exc: write(cell_dir/'victoriametrics-request-count.error',str(exc))


def evidence_format(kind: str) -> str:
    return 'K6' if kind in ('k6','warmup') else 'LOCUST' if kind == 'locust' else 'UNKNOWN'


def job_and_ledger(job: dict, ns: str, cell_dir: Path, kind: str) -> tuple[str,str]:
    apply(job)
    name=job['metadata']['name']
    start=utc()
    sampler_stop=threading.Event()
    sampler=None
    if kind=='k6':
        def sample_kubelet():
            try:
                node=kube_json('get','nodes')['items'][0]['metadata']['name']
                with (cell_dir/'kubelet-during-k6.ndjson').open('w') as handle:
                    while not sampler_stop.is_set():
                        result=kube('get','--raw',f'/api/v1/nodes/{node}/proxy/stats/summary',check=False)
                        if result.returncode==0:
                            handle.write(json.dumps({'captured_at':utc(),'summary':json.loads(result.stdout)})+'\n')
                            handle.flush()
                        if sampler_stop.wait(15): break
            except Exception as exc:
                write(cell_dir/'kubelet-sampling.error',str(exc))
        sampler=threading.Thread(target=sample_kubelet,daemon=True)
        sampler.start()
    try:
        completed=kube('wait','--for=condition=complete',f'job/{name}','-n',ns,'--timeout=360s',check=False)
    finally:
        sampler_stop.set()
        if sampler: sampler.join(timeout=5)
    end=utc()
    logs=kube('logs',f'job/{name}','-n',ns,'--all-containers=false',check=False).stdout
    write(cell_dir/f'{kind}.log',logs)
    if completed.returncode:
        write(cell_dir/f'{kind}.error',completed.stderr)
        raise RuntimeError(f'{kind} Job did not complete: {completed.stderr[-800:]}')
    if evidence_format(kind)=='K6':
        write(cell_dir/f'{kind}.ndjson',extract_marked(logs,'K6'))
    elif evidence_format(kind)=='LOCUST':
        for part in ['stats','failures','exceptions','stats_history']:
            write(cell_dir/f'locust-{part}.csv',extract_marked(logs,'LOCUST_'+part))
    return start,end


def checksums(path: Path) -> None:
    lines=[]
    for item in sorted(path.rglob('*')):
        if item.is_file() and item.name!='SHA256SUMS':
            lines.append(f'{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.relative_to(path)}')
    write(path/'SHA256SUMS','\n'.join(lines)+'\n')


def run_cell(cell: Cell, parent: Path, *, smoke: bool, replace_existing: bool, deadline: float) -> dict:
    cell_dir=parent/('smoke' if smoke else 'measured')/cell.slug
    cell_dir.mkdir(parents=True,exist_ok=True)
    status={'cell':cell.slug,'phase':'reset','started_at':utc(),'smoke':smoke}
    write(cell_dir/'status.json',status)
    try:
        def gate(phase: str):
            if time.monotonic() >= deadline: raise TimeoutError('eight-hour study deadline reached')
            status['phase']=phase
            write(cell_dir/'status.json',status)
        gate('reset')
        reset_cell(replace_existing)
        prepare_source(cell.source)
        create_namespace(cell)
        gate('demo')
        install_demo(cell,cell_dir)
        gate('karthos')
        deploy_karthos(cell,cell_dir)
        target='http://frontend:80' if cell.demo=='boutique' else 'http://frontend-proxy:8080'
        apply({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'eval-k6-script','namespace':cell.namespace,
               'labels':{OWNER_LABEL:OWNER_VALUE}},'data':{'run.js':(ROOT/'evaluation/workloads/run.js').read_text()}})
        gate('warmup')
        warm=k6_job(cell.demo,cell.source,'warm',cell.namespace,CONFIG['k6_image'],target)
        for env in warm['spec']['template']['spec']['containers'][0]['env']:
            if env['name']=='DURATION':env['value']='20s'
            if env['name']=='RATE':env['value']='1'
        job_and_ledger(warm,cell.namespace,cell_dir,'warmup')
        gate('k6')
        measured=k6_job(cell.demo,cell.source,'measure',cell.namespace,CONFIG['k6_image'],target)
        if smoke:
            for env in measured['spec']['template']['spec']['containers'][0]['env']:
                if env['name']=='DURATION':env['value']='30s'
                if env['name']=='RATE':env['value']='1'
        start,end=job_and_ledger(measured,cell.namespace,cell_dir,'k6')
        manifest={'cell':cell.slug,'demo':cell.demo,'source':cell.source,'repeat':cell.repeat,
                  'start':start,'end':end,'namespace':cell.namespace,'source_id':source_id(cell.source),
                  'karthos_revision':cmd(['git','rev-parse','HEAD'],cwd=REPO).stdout.strip()}
        write(cell_dir/'manifest.json',manifest)
        gate('drain')
        for _ in range(CONFIG['telemetry_drain_seconds']//10):
            if time.monotonic()>=deadline:raise TimeoutError('eight-hour study deadline reached during drain')
            time.sleep(10)
        gate('capture')
        capture(cell,cell_dir,start,end)
        raw_api=json.loads((cell_dir/'api-request-raw.json').read_text()) if (cell_dir/'api-request-raw.json').exists() else {}
        if not raw_api.get('data',{}).get('samples'):
            raise RuntimeError('no Karthos servicegraph request samples in smoke/measurement window')
        source_log=(cell_dir/'source-traces.log').read_text() if (cell_dir/'source-traces.log').exists() else ''
        if cell.source=='trace' and not source_log.strip():
            raise RuntimeError('trace source log is empty')
        if cell.source=='mesh' and not any('istio_requests_total' in p.read_text() for p in (cell_dir/'istio').glob('*.prom')):
            raise RuntimeError('no Istio sidecar request counter was captured')
        if not smoke:
            gate('verify')
            write(cell_dir/'verification.json',evaluate_bundle(cell_dir,CONFIG['thresholds']))
            if cell.repeat==1:
                gate('locust')
                image=CONFIG[f'{cell.demo}_locust_image']
                job_and_ledger(locust_job(cell.demo,'realism',cell.namespace,image,target),cell.namespace,cell_dir,'locust')
        status.update(phase='complete',finished_at=utc())
    except BaseException as exc:
        status.update(phase='failed',failed_at=utc(),error=str(exc))
        write(cell_dir/'status.json',status)
        checksums(cell_dir)
        raise
    write(cell_dir/'status.json',status)
    checksums(cell_dir)
    return status


def run(args) -> None:
    global _DEADLINE
    OUT.mkdir(exist_ok=True)
    run_dir=OUT/(args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    if run_dir.exists(): raise RuntimeError(f'run directory already exists: {run_dir}')
    run_dir.mkdir()
    deadline=time.monotonic()+CONFIG['study_deadline_seconds']
    _DEADLINE=deadline
    report={'run_dir':str(run_dir),'started_at':utc(),'cells':[],'state':'running'}
    write(run_dir/'run-report.json',report)
    try:
        report['preflight']=preflight(args.replace_existing)
        report['pins']=stage()
        write(run_dir/'locked-config.json',CONFIG)
        shutil.copy2(ROOT/'evaluation/workloads/run.js',run_dir/'locked-k6-run.js')
        scoring=run_dir/'scoring-source'
        scoring.mkdir()
        for name in ('artifacts.py','verify.py','runner.py','manifests.py','protocol.py'):
            shutil.copy2(ROOT/'evaluation'/name,scoring/name)
        # Images are built from the current checkout without editing it.
        cmd([str(REPO/'test/e2e/prepare-platform-images.sh')],cwd=REPO,
            capture=run_dir/'image-build.log',timeout=1800)
        for cell in cells_for_repeat(1):
            report['cells'].append(run_cell(cell,run_dir,smoke=True,
                            replace_existing=args.replace_existing,deadline=deadline))
            write(run_dir/'run-report.json',report)
        write(run_dir/'frozen-thresholds.json',CONFIG['thresholds'])
        if not args.smoke_only:
            for repeat in range(1,4):
                for cell in cells_for_repeat(repeat):
                    report['cells'].append(run_cell(cell,run_dir,smoke=False,
                                 replace_existing=args.replace_existing,deadline=deadline))
                    write(run_dir/'run-report.json',report)
        if not args.smoke_only:
            verify(run_dir)
            report['aggregate']=json.loads((run_dir/'aggregate-verification.json').read_text())
        report['state']='complete' if not args.smoke_only else 'smoke_complete'
    except BaseException as exc:
        report['state']='incomplete'
        report['error']=str(exc)
        raise
    finally:
        report['finished_at']=utc()
        report['attempts']=attempt_statuses(run_dir)
        write(run_dir/'run-report.json',report)
        checksums(run_dir)
        _DEADLINE=None
        print(f'evidence: {run_dir}',flush=True)


def resume(run_dir: Path) -> None:
    """Continue an incomplete measured matrix without discarding earlier cells."""
    global _DEADLINE
    run_dir=run_dir.resolve()
    if not run_dir.is_dir(): raise RuntimeError(f'run directory missing: {run_dir}')
    report=json.loads((run_dir/'run-report.json').read_text())
    if report.get('state')!='incomplete': raise RuntimeError('only an incomplete run can be resumed')
    if json.loads((run_dir/'locked-config.json').read_text())!=CONFIG:
        raise RuntimeError('run configuration changed; refusing to resume')
    if (run_dir/'locked-k6-run.js').read_bytes()!=(ROOT/'evaluation/workloads/run.js').read_bytes():
        raise RuntimeError('k6 workload changed; refusing to resume')
    if not (run_dir/'frozen-thresholds.json').exists():
        raise RuntimeError('measured run has no frozen thresholds')
    if json.loads((run_dir/'frozen-thresholds.json').read_text())!=CONFIG['thresholds']:
        raise RuntimeError('frozen thresholds differ from the current configuration')
    for cell in cells_for_repeat(1):
        status=run_dir/'smoke'/cell.slug/'status.json'
        if not status.exists() or json.loads(status.read_text()).get('phase')!='complete':
            raise RuntimeError(f'smoke gate incomplete: {cell.slug}')
    elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(report['started_at'].replace('Z','+00:00'))).total_seconds()
    remaining=CONFIG['study_deadline_seconds']-elapsed
    if remaining<=0: raise TimeoutError('original eight-hour study deadline reached')
    _DEADLINE=time.monotonic()+remaining
    preflight(False)
    if stage()!=report['pins']: raise RuntimeError('pinned source versions changed')
    scoring=run_dir/'scoring-source'
    scoring.mkdir(exist_ok=True)
    for name in ('artifacts.py','verify.py','runner.py','manifests.py','protocol.py'):
        shutil.copy2(ROOT/'evaluation'/name,scoring/name)
    report['state']='running'
    report.setdefault('resumed_at',[]).append(utc())
    write(run_dir/'run-report.json',report)
    try:
        for repeat in range(1,4):
            for cell in cells_for_repeat(repeat):
                cell_dir=run_dir/'measured'/cell.slug
                status=cell_dir/'status.json'
                if status.exists() and json.loads(status.read_text()).get('phase')=='complete':
                    continue
                if cell_dir.exists():
                    saved=run_dir/'failed-attempts'
                    saved.mkdir(exist_ok=True)
                    index=1
                    while (saved/f'{cell.slug}-{index}').exists(): index+=1
                    cell_dir.rename(saved/f'{cell.slug}-{index}')
                report['cells'].append(run_cell(cell,run_dir,smoke=False,
                                                 replace_existing=False,deadline=_DEADLINE))
                write(run_dir/'run-report.json',report)
        verify(run_dir)
        report['aggregate']=json.loads((run_dir/'aggregate-verification.json').read_text())
        report['state']='complete'
    except BaseException as exc:
        report['state']='incomplete'
        report['error']=str(exc)
        raise
    finally:
        report['finished_at']=utc()
        report['attempts']=attempt_statuses(run_dir)
        write(run_dir/'run-report.json',report)
        checksums(run_dir)
        _DEADLINE=None
        print(f'evidence: {run_dir}',flush=True)


def attempt_statuses(run_dir: Path) -> list[dict]:
    paths=[]
    for pattern in ('smoke/r*/status.json','measured/r*/status.json',
                    'failed-attempts/*/status.json'):
        paths.extend(run_dir.glob(pattern))
    return [json.loads(p.read_text()) for p in sorted(paths)]


def verify(run_dir: Path) -> None:
    frozen=run_dir/'frozen-thresholds.json'
    if not frozen.exists():
        raise RuntimeError('this run has no frozen thresholds; measured results cannot be rescored')
    thresholds=json.loads(frozen.read_text())
    reports=[]
    for cell in sorted((run_dir/'measured').glob('*')):
        if (cell/'manifest.json').exists() and (cell/'k6.ndjson').exists():
            result=evaluate_bundle(cell,thresholds)
            write(cell/'verification.json',result)
            reports.append(result)
            checksums(cell)
    by_pair={}
    for report in reports:
        slug=report['cell']
        pair='-'.join(slug.split('-')[1:]) if slug else 'unknown'
        by_pair.setdefault(pair,[]).append(report)
    groups={}
    for pair,items in sorted(by_pair.items()):
        errors=[x['requests']['comparison'].get('absolute_error') for x in items
                if x['requests']['comparison']['state']!='missing']
        groups[pair]={'repeats':len(items),'pass':sum(x['state']=='pass' for x in items),
                      'median_checkout_absolute_error':statistics.median(errors) if errors else None}
    write(run_dir/'aggregate-verification.json',{'cells':reports,'count':len(reports),
          'pass':sum(r['state']=='pass' for r in reports),'fail':sum(r['state']=='fail' for r in reports),
          'incomplete':sum(r['state']=='incomplete' for r in reports),'by_pair':groups})
    report_path=run_dir/'run-report.json'
    if report_path.exists():
        run_report=json.loads(report_path.read_text())
        run_report['aggregate']=json.loads((run_dir/'aggregate-verification.json').read_text())
        run_report['attempts']=attempt_statuses(run_dir)
        run_report['verified_at']=utc()
        write(report_path,run_report)
    checksums(run_dir)


def cleanup(replace_existing: bool) -> None:
    check_ownership(replace_existing)
    reset_cell(replace_existing)
    # Istio cleanup is limited to the Karthos E2E ownership marker.
    cmd([str(REPO/'test/e2e/cluster/istio.sh'),'reset-if-owned'],cwd=REPO,timeout=600)
    if ownership():
        kube('delete','configmap',MARKER,'-n',KARTHOS_NAMESPACE,'--ignore-not-found')


def main(argv=None) -> None:
    parser=argparse.ArgumentParser(description='Kivel Karthos evaluation')
    commands=parser.add_subparsers(dest='command',required=True)
    pre=commands.add_parser('preflight')
    pre.add_argument('--replace-existing',action='store_true')
    run_parser=commands.add_parser('run')
    run_parser.add_argument('--smoke-only',action='store_true')
    run_parser.add_argument('--replace-existing',action='store_true')
    run_parser.add_argument('--run-id')
    resume_parser=commands.add_parser('resume')
    resume_parser.add_argument('run_dir',type=Path)
    ver=commands.add_parser('verify')
    ver.add_argument('run_dir',type=Path)
    clean=commands.add_parser('cleanup')
    clean.add_argument('--replace-existing',action='store_true')
    args=parser.parse_args(argv)
    if args.command=='preflight': print(json.dumps(preflight(args.replace_existing),indent=2))
    elif args.command in ('run','resume','verify'):
        from . import pilot
        version = CONFIG.get('protocol_version',1)
        if args.command in ('resume','verify'):
            locked=args.run_dir/'locked-config.json'
            if locked.exists(): version=json.loads(locked.read_text()).get('protocol_version',1)
        if version >= 2:
            if args.command=='run': pilot.run_study(args)
            elif args.command=='resume': pilot.resume_study(args.run_dir)
            else: pilot.verify_study(args.run_dir)
        elif args.command=='run': run(args)
        elif args.command=='resume': resume(args.run_dir)
        else: verify(args.run_dir)
    elif args.command=='cleanup': cleanup(args.replace_existing)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'evaluation failed: {exc}',file=sys.stderr)
        sys.exit(1)
