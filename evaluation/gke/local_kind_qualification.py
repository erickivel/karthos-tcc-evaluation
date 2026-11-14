"""Fresh, disposable kind capture for local structure and source arbitration.

Uses existing product fixtures and public discovery/deployment interfaces. A
verified business-namespace capability profile is explicit and never reported
as a successful default cluster-wide mesh detection.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from .models import read,write,sha
from .bundle import _product
from .thesis import openapi_projection
from ..diagnostics.thesis_kind import api_forward,capture as capture_replacement
from ..diagnostics.thesis_local import kind_structure,structure,arbitration

BUSINESS_APPS={'gateway-go','service-a-python','service-b-node','mesh-protocol-server'}
METRICS=('servicegraph.request.count','servicegraph.request.error.count','servicegraph.request.duration')


def business_profile(profile,checks):
    if {r['app'] for r in checks}!=BUSINESS_APPS or not all(r['proxy_ready'] and r['counter_series']>0 for r in checks):
        raise ValueError('complete actual business mesh observations required')
    result=copy.deepcopy(profile)
    mesh=next(r for r in result['capabilities'] if r['source_id']=='mesh.istio')
    if mesh['status'] not in ('available','partial'):raise ValueError('mesh detector did not find available/partial mesh')
    mesh.update(status='available',signals=['metric'])
    # Provisional trace availability is verified against actual receiver counters
    # before a stage can be accepted or receipt emitted.
    trace=next(r for r in result['capabilities'] if r['source_id']=='traces.servicegraph')
    trace.update(status='available',signals=['metric'],evidence={'otlp':{'traces_enabled':True}})
    return result


def responses(text):
    rows=[]
    for line in text.splitlines():
        if '{"gateway"' in line:
            rows.append(json.JSONDecoder().raw_decode(line[line.index('{"gateway"'):])[0])
    if len(rows)!=40 or sum(r.get('mode')=='intentional-error' for r in rows)!=10:
        raise ValueError('expected 40 actual gateway responses, including 10 intentional errors')
    for row in rows:
        expected=500 if row.get('mode')=='intentional-error' else 200
        if row.get('gateway')!='gateway-go' or not row.get('results') or any(r.get('status')!=expected for r in row['results']):
            raise ValueError('actual fixture outcomes differ from declared journey')
    return rows


def account_delta(before,after,source):
    delta={key:after.get(key,0)-before.get(key,0) for key in before.keys()|after.keys()}
    if delta.get(source)!=40 or any(value!=0 for key,value in delta.items() if key!=source):
        raise ValueError('expected one selected source contribution of 40; reset, loss or duplication observed')
    return delta[source]


def _request(url):
    with urllib.request.urlopen(url,timeout=10) as response:
        data=response.read(16*1024*1024+1)
    if len(data)>16*1024*1024:raise ValueError('local response bound exceeded')
    return data.decode()


@contextmanager
def forward(kube,target,port,out,label,namespace='karthos-system'):
    log=out/(label+'-forward.log')
    with log.open('w') as stream:
        child=subprocess.Popen([*kube,'port-forward','-n',namespace,target,':'+str(port)],stdout=stream,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+15
            while True:
                match=re.search(r'Forwarding from 127.0.0.1:(\d+)',log.read_text())
                if match:break
                if child.poll() is not None or time.monotonic()>deadline:raise RuntimeError('port-forward unavailable: '+label)
                time.sleep(.1)
            yield 'http://127.0.0.1:'+match[1]
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)


class KindCapture:
    def __init__(self,product,lock,out,name):
        self.product=Path(product).resolve();self.pins=read(lock);self.out=Path(out).resolve();self.name=name
        if self.out==self.product or self.product in self.out.parents:raise ValueError('kind output must be outside the immutable product source')
        if not re.fullmatch(r'karthos-thesis-[a-z0-9-]{1,25}',name):raise ValueError('explicit disposable karthos-thesis-* name required')
        files,missing,revision,pending=_product(self.product,self.pins)
        if missing or pending or not files or revision!=self.pins['karthos_revision']:raise ValueError('pinned product source closure required')
        self.real_kubectl=shutil.which('kubectl')
        if not self.real_kubectl:raise ValueError('kubectl unavailable')
        self.kubeconfig=self.out/'kubeconfig';self.context='kind-'+name
        self.kube=[self.real_kubectl,'--kubeconfig',str(self.kubeconfig),'--context',self.context]
        self.commands=[];self.cluster_nodes=None;self.created=False
        self.tag='thesis-'+self.pins['karthos_revision'][:12]+'-'+name.removeprefix('karthos-thesis-')

    def run(self,args,log,*,timeout=600):
        path=self.out/log;path.parent.mkdir(parents=True,exist_ok=True);started=time.time()
        with path.open('w') as stream:
            child=subprocess.Popen(args,cwd=self.product,env=self.env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:code=child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                import signal
                os.killpg(child.pid,signal.SIGTERM)
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                raise
            finally:
                self.commands.append({'argv':args,'started_at':started,'finished_at':time.time(),'returncode':child.returncode,'log':log})
                write(self.out/'commands.json',self.commands)
        if code:raise RuntimeError('local kind command failed: '+log)
        return path

    def get(self,*args):
        return json.loads(subprocess.check_output([*self.kube,*args,'-o','json'],env=self.env,text=True,timeout=30))

    def setup(self):
        existing=subprocess.check_output(['kind','get','clusters'],text=True).splitlines()
        if self.name in existing:raise ValueError('refusing to reuse an existing kind cluster')
        self.out.mkdir(parents=True,exist_ok=False)
        write(self.out/'product-pin.json',self.pins)
        # Product fixture image and chart scripts may change their working tree.
        # Preserve the immutable build source used by later preparation.
        source=self.product
        self.product=self.out/'product'
        shutil.copytree(source,self.product,ignore=shutil.ignore_patterns('.git'))
        bin_dir=self.out/'bin';bin_dir.mkdir()
        # The product fixtures have explicit karthos-test hostnames. Adapt only
        # those rendered nodeSelector values, recording every rendered artifact.
        wrapper=bin_dir/'kubectl'
        wrapper.write_text('#!'+sys.executable+'\n'+_WRAPPER.replace('CONFIG_VALUE',repr({'kubectl':self.real_kubectl,'kubeconfig':str(self.kubeconfig),'context':self.context,'name':self.name,'out':str(self.out)})))
        wrapper.chmod(0o755)
        profile=self.out/'profile.env'
        profile.write_text('\n'.join(['CLUSTER_ENGINE=kind','CLUSTER_LOCATION=local','KIND_CLUSTER_NAME='+self.name,
            'IMAGE_TAG='+self.tag,'KUBECONFIG='+shlex.quote(str(self.kubeconfig)),'GATEWAY_LOCAL_PORT=18180','BACKEND_API_LOCAL_PORT=18181'])+'\n')
        self.env={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'KUBECONFIG':str(self.kubeconfig),
            'KARTHOS_CLUSTER_PROFILE_OVERRIDE':self.name,'KARTHOS_CLUSTER_PROFILE_FILE_OVERRIDE':str(profile),
            'KARTHOS_IMAGE_TAG_OVERRIDE':self.tag,'IMAGE_TAG':self.tag,'REGISTRY_HOST':'localhost:5001',
            'KIND_NAMESPACE':'karthos-system','HELM_RELEASE_NAME':'karthos','GATEWAY_LOCAL_PORT':'18180','CLUSTER_ENGINE':'kind'}
        # The existing setup script creates/reuses the documented local registry;
        # it is shared infrastructure and is deliberately never removed here.
        try:self.run(['task','test:e2e:setup'],'setup.log',timeout=420)
        finally:
            self.created=self.name in subprocess.check_output(['kind','get','clusters'],text=True).splitlines()
        nodes=self.get('get','nodes');self.cluster_nodes={n['metadata']['name']:n['metadata']['uid'] for n in nodes['items']}
        write(self.out/'nodes-created.json',nodes)
        self.run(['go','build','-o',str(self.out/'discovery-job'),'./cmd/discovery-job'],'discovery-build.log')
        self.run(['bash','test/e2e/prepare-platform-images.sh'],'product-images.log',timeout=1800)
        self.run(['helm','dependency','build','deploy/helm/karthos'],'chart-dependencies.log')

    def discover(self,stage):
        stage.mkdir(parents=True,exist_ok=False)
        path=stage/'profile.json'
        self.run([str(self.out/'discovery-job'),'--mode=discover','--kubeconfig='+str(self.kubeconfig),
            '--cluster-name='+self.name,'--output='+str(path),'--namespace=karthos-system','--target-namespace=karthos-demo'],
            str(stage.relative_to(self.out)/'discovery.log'),timeout=90)
        return read(path)

    def deploy(self,stage,profile):
        write(stage/'effective-profile.json',profile)
        self.run([str(self.out/'discovery-job'),'--mode=deploy','--capability-profile='+str(stage/'effective-profile.json'),
            '--values-output='+str(stage/'otel-values.yaml'),'--namespace=karthos-system','--release-name=karthos',
            '--chart-path='+str(self.product/'deploy/helm/karthos'),'--helm-timeout=180s',
            '--backend-image-repository=localhost:5001/karthos-backend','--backend-image-tag='+self.tag,
            '--frontend-image-repository=localhost:5001/karthos-frontend','--frontend-image-tag='+self.tag,
            '--backend-image-pull-policy=Always','--frontend-image-pull-policy=Always','--storage-backend=external',
            '--postgresql-mode=bundled','--victoriametrics-mode=bundled','--servicegraph-store-max-items=10000'],
            str(stage.relative_to(self.out)/'deploy.log'),timeout=300)
        for component in ('backend','gateway','agent'):
            kind='daemonset' if component=='agent' else 'deployment'
            self.run([*self.kube,'rollout','status',kind+'/karthos-'+component,'-n','karthos-system','--timeout=180s'],str(stage.relative_to(self.out)/(component+'-rollout.log')),timeout=190)
        for name in ('karthos-source-contract','karthos-gateway-config','karthos-agent-config'):
            write(stage/(name+'.json'),self.get('get','configmap',name,'-n','karthos-system'))
        generated=read(stage/'otel-values.yaml.source-plan.json')
        mounted=json.loads(read(stage/'karthos-source-contract.json')['data']['source-plan.json'])
        if generated!=mounted:raise ValueError('generated/mounted source plans differ')
        return mounted

    def traffic(self,stage,normal,errors,label):
        return self.run(['task','test:e2e:traffic','GATEWAY_LOCAL_PORT=18180','TRAFFIC_REQUESTS='+str(normal),
            'TRAFFIC_ERROR_REQUESTS='+str(errors),'TRAFFIC_INTERVAL=0.2','TRAFFIC_INTERLEAVE_ERRORS=true'],
            str(stage.relative_to(self.out)/(label+'.log')),timeout=120)

    def mesh_checks(self,stage):
        pods=self.get('get','pods','-n','karthos-demo');write(stage/'business-pods.json',pods);checks=[]
        for pod in pods['items']:
            app=pod['metadata'].get('labels',{}).get('app')
            if app not in BUSINESS_APPS or pod['metadata'].get('deletionTimestamp'):continue
            states=pod['status'].get('containerStatuses',[])+pod['status'].get('initContainerStatuses',[])
            ready=any(s['name']=='istio-proxy' and s.get('ready') for s in states)
            with forward(self.kube,'pod/'+pod['metadata']['name'],15020,stage,pod['metadata']['name'],'karthos-demo') as endpoint:
                raw=_request(endpoint+'/stats/prometheus');(stage/(pod['metadata']['name']+'.prom')).write_text(raw)
                count=sum(line.startswith('istio_requests_total{') for line in raw.splitlines())
            checks.append({'pod':pod['metadata']['name'],'uid':pod['metadata']['uid'],'app':app,'proxy_ready':ready,'counter_series':count})
        write(stage/'business-mesh-checks.json',checks);return checks

    def accounting(self,stage,source,plan):
        assigned={metric:[plan['metric_assignments'][metric]] for metric in METRICS}
        if any(value!=[source] for value in assigned.values()):raise ValueError('source plan differs from stage')
        self.traffic(stage,1,1,'warmup')
        with forward(self.kube,'svc/karthos-victoria-metrics-single-server',8428,stage,'vm') as vm:
            def observed():
                query='sum by (source) (servicegraph_request_count{edge_client="gateway-go",edge_server="service-a-python",temporality!="delta",karthos_unit="count"})'
                started=time.time();payload=json.loads(_request(vm+'/api/v1/query?'+urllib.parse.urlencode({'query':query})))
                data={r['metric']['source']:float(r['value'][1]) for r in payload['data']['result']}
                with (stage/'counter-polls.ndjson').open('a') as stream:
                    stream.write(json.dumps({'started_at':started,'finished_at':time.time(),'query':query,'response':payload})+'\n')
                return data
            deadline=time.monotonic()+120;previous=None;stable_at=None
            while time.monotonic()<deadline:
                current=observed()
                if source in current and current==previous:
                    if stable_at is not None and time.monotonic()-stable_at>=30:break
                else:previous=current;stable_at=time.monotonic()
                time.sleep(3)
            else:raise TimeoutError('quiet source-matched baseline unavailable')
            before=current;ledger=responses(self.traffic(stage,30,10,'measured').read_text())
            write(stage/'actual-generator-responses.json',ledger)
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                after=observed()
                try:account_delta(before,after,source);break
                except ValueError:time.sleep(3)
            else:raise ValueError('selected source accounting did not match actual calls')
        with forward(self.kube,'deployment/karthos-backend',8080,stage,'api') as api:
            for metric in METRICS:
                query={'metric_name':metric,'source':source,'page_size':1000}
                if metric!='servicegraph.request.duration':query['aggregation']='latest'
                response=json.loads(_request(api+'/api/v1/metrics?'+urllib.parse.urlencode(query)))
                write(stage/(metric+'.json'),response)
                samples=response.get('data',{}).get('samples',[])
                if not samples or any(r.get('labels',{}).get('source')!=source for r in samples):raise ValueError('API source provenance missing/mismatched')
        return {'reference_calls':len(ledger),'observed_calls':account_delta(before,after,source),'before':before,'after':after,'metric_contributions':assigned}

    def execute(self):
        stages=[];report={'state':'incomplete','scope':'verified business namespace; not default cluster-wide mesh detection'}
        try:
            self.setup()
            self.run(['bash','test/e2e/cluster/istio.sh','install'],'istio-install.log',timeout=420)
            self.run(['bash','test/e2e/apps/deploy.sh','with-istio'],'mesh-fixtures.log',timeout=1800)
            stage=self.out/'mesh';profile=self.discover(stage)
            self.traffic(stage,1,1,'preprofile-traffic')
            profile=business_profile(profile,self.mesh_checks(stage));plan=self.deploy(stage,profile)
            self.traffic(stage,1,1,'trace-intake-traffic')
            with forward(self.kube,'deployment/karthos-gateway',8888,stage,'gateway-selfmetrics') as endpoint:
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    raw=_request(endpoint+'/metrics');(stage/'receiver-selfmetrics.prom').write_text(raw)
                    if sum(float(line.rsplit(' ',1)[-1]) for line in raw.splitlines() if line.startswith('otelcol_receiver_accepted_spans'))>0:break
                    time.sleep(2)
                else:raise ValueError('provisional trace availability lacked actual receiver evidence')
            # Advertise actual observed trace intake for the subsequent public discovery.
            self.run([*self.kube,'annotate','service','gateway-go','-n','karthos-demo','karthos.io/otlp-traces=true','--overwrite'],'trace-annotation.log')
            account=self.accounting(stage,'mesh.istio',plan)
            stages.append({**account,'available_sources':['mesh.istio','traces.servicegraph'],'configuration_sha256':sha(stage/'otel-values.yaml')})
            replacement=self.out/'replacement';replacement.mkdir()
            image=json.loads(subprocess.check_output(['docker','image','inspect','localhost:5001/service-b-node:'+self.tag],text=True))[0]
            image=next(d for d in image['RepoDigests'] if d.startswith('localhost:5001/service-b-node@sha256:'))
            contract=openapi_projection(self.product/'docs/openapi.yaml',self.pins['openapi_sha256'])
            with api_forward(self.kube,contract,replacement) as client:
                captured=capture_replacement(self.kube,image,client,replacement);write(replacement/'replacement.json',captured)
            pods=self.get('get','pods','-n','karthos-demo','-l','app=gateway-go')['items']
            if len(pods)!=1:raise ValueError('gateway Pod fixture scope is ambiguous')
            pod=pods[0];owner=next(o for o in pod['metadata']['ownerReferences'] if o.get('controller'))
            rs=self.get('get','replicaset',owner['name'],'-n','karthos-demo')
            record=kind_structure(pod,rs,captured,read(replacement/'placement-before.json')['graph'])
            if structure(record)['state']!='pass':raise ValueError('live structural fixture did not qualify')
            write(self.out/'structure.json',record)
            shutil.copy2(replacement/'placement-retained.json',self.out/'placement-retained.json')
            transition=[]
            self.run(['bash','test/e2e/cluster/istio.sh','reset-if-owned'],'remove-owned-istio.log',timeout=300)
            transition.append({'step':'istio-unavailable','at':time.time()})
            self.run([*self.kube,'label','namespace','karthos-demo','istio-injection-'],'remove-injection.log')
            self.run(['bash','test/e2e/apps/deploy.sh','with-prometheus'],'trace-fixtures.log',timeout=1800)
            stage=self.out/'trace';profile=self.discover(stage)
            available={r['source_id'] for r in profile['capabilities'] if r['status']=='available'}
            if 'mesh.istio' in available or 'traces.servicegraph' not in available:raise ValueError('explicit rediscovery did not select trace-only availability')
            transition.append({'step':'rediscovered','at':time.time()});plan=self.deploy(stage,profile)
            transition.append({'step':'redeployed','at':time.time()});write(self.out/'transition.json',transition)
            account=self.accounting(stage,'traces.servicegraph',plan)
            stages.append({**account,'available_sources':['traces.servicegraph'],'configuration_sha256':sha(stage/'otel-values.yaml'),
                           'explicit_rediscovery':True,'explicit_redeployment':True})
            record={'stages':stages,'scope':report['scope'],'transition_reference':'transition.json'}
            if arbitration(record)['state']!='pass':raise ValueError('live source arbitration did not qualify')
            write(self.out/'arbitration.json',record);report['state']='pass'
        except Exception as exc:
            report['error']=str(exc)
        finally:
            if self.out.exists():
                cleanup={'removed':False,'shared_registry_retained':True}
                try:
                    if self.created:
                        nodes=self.get('get','nodes')
                        current={n['metadata']['name']:n['metadata']['uid'] for n in nodes['items']}
                        if self.cluster_nodes is None or current!=self.cluster_nodes:raise ValueError('owned cluster identity unavailable/changed; manual cleanup required')
                        self.run(['kind','delete','cluster','--name',self.name],'cleanup.log',timeout=180)
                        cleanup['removed']=self.name not in subprocess.check_output(['kind','get','clusters'],text=True).splitlines()
                    else:cleanup['removed']=True
                except Exception as exc:cleanup['error']=str(exc)
                report['cleanup']=cleanup
                if not cleanup['removed']:report['state']='incomplete'
                write(self.out/'kind-report.json',report)
        return report


_WRAPPER='''import os,sys,subprocess,re,time,json,pathlib
cfg=CONFIG_VALUE
args=sys.argv[1:]
if args and args[0]=='kustomize':
 result=subprocess.run([cfg['kubectl']]+args,capture_output=True,text=True)
 sys.stderr.write(result.stderr)
 if result.returncode:sys.exit(result.returncode)
 before=result.stdout;after=before
 for suffix in ('control-plane','worker'):
  after=re.sub(r'(?m)^(\\s+kubernetes\\.io/hostname:\\s*)karthos-test-'+suffix+r'(\\s*)$',lambda m:m[1]+cfg['name']+'-'+suffix+m[2],after)
 target=pathlib.Path(cfg['out'])/('render-'+str(time.time_ns()))
 target.with_suffix('.before.yaml').write_text(before);target.with_suffix('.after.yaml').write_text(after)
 sys.stdout.write(after);sys.exit(0)
os.execv(cfg['kubectl'],[cfg['kubectl'],'--kubeconfig='+cfg['kubeconfig'],'--context='+cfg['context']]+args)
'''


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--product',type=Path,required=True);p.add_argument('--product-lock',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--cluster-name',required=True)
    args=p.parse_args();report=KindCapture(args.product,args.product_lock,args.out,args.cluster_name).execute()
    print(json.dumps(report,indent=2));return 0 if report['state']=='pass' else 2

if __name__=='__main__':raise SystemExit(main())
