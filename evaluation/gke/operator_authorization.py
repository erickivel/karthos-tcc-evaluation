"""Operator-only RBAC bootstrap through an ephemeral, verified private API tunnel.

No operator token, credential file, or kubeconfig is sent to the controller.
"""
from __future__ import annotations
import base64,datetime,hashlib,http.client,ipaddress,json,os,re,signal,socket,ssl,subprocess,time
from pathlib import Path
from .operator_monitor import Operator
from .iap_recovery import recover
from .models import identifier,canonical_sha
from .cloud_identity import sanitized_error

BINDING='karthos-evaluation-controller-bootstrap'
OWNER='gke-qualification-v1'
RBAC='/apis/rbac.authorization.k8s.io/v1/clusterrolebindings'
EMAIL_SCOPE='https://www.googleapis.com/auth/userinfo.email'


class APIError(RuntimeError):
    def __init__(self,status):
        self.status=status
        super().__init__('Kubernetes API returned HTTP '+str(status))


def _network(value):
    return str(value).removeprefix('https://www.googleapis.com/compute/v1/').removeprefix('https://compute.googleapis.com/compute/v1/')


def validate_target(args,cluster,vm,account):
    labels=cluster.get('resourceLabels',{})
    if (args.owner_id!=OWNER or cluster.get('name')!=args.cluster or str(cluster.get('id'))!=args.cluster_uid
            or cluster.get('location',cluster.get('zone'))!=args.zone or cluster.get('status')!='RUNNING'
            or labels.get('evaluation-owner')!=OWNER or labels.get('evaluation-run')!=args.run_id):
        raise ValueError('cluster UID, ownership, location or readiness mismatch')
    if str(vm.get('id'))!=args.uid:raise ValueError('controller UID changed')
    accounts=vm.get('serviceAccounts',[]);networks=vm.get('networkInterfaces',[])
    if len(accounts)!=1 or accounts[0].get('email')!=account or EMAIL_SCOPE not in accounts[0].get('scopes',[]):
        raise ValueError('controller service account or userinfo-email scope mismatch')
    expected_network=_network(cluster.get('network'))
    if '/' not in expected_network:expected_network='projects/'+args.project+'/global/networks/'+expected_network
    if len(networks)!=1 or _network(networks[0].get('network'))!=expected_network or not expected_network.startswith('projects/'+args.project+'/global/networks/'):
        raise ValueError('controller and cluster network mismatch')
    private=cluster.get('privateClusterConfig',{})
    if private.get('enablePrivateEndpoint') is not True:raise ValueError('private cluster endpoint required')
    endpoint=str(private.get('privateEndpoint',''))
    try:address=ipaddress.ip_address(endpoint)
    except ValueError:raise ValueError('valid private cluster endpoint required') from None
    if address.version!=4 or not address.is_private:raise ValueError('private IPv4 endpoint required')
    ca=cluster.get('masterAuth',{}).get('clusterCaCertificate')
    if not isinstance(ca,str) or not ca:raise ValueError('cluster CA missing')
    return endpoint,ca


def validate_lease(args,saved,value,generation,now):
    expected={'project_id':args.project,'owner_id':args.owner_id,'run_id':args.run_id,'token':saved.get('token'),'phase':'active'}
    if not saved.get('token') or any(value.get(k)!=v for k,v in expected.items()):raise ValueError('active project lease ownership mismatch')
    if str(saved.get('generation'))!=str(generation):raise ValueError('project lease generation changed')
    if type(value.get('deadline')) not in (int,float) or value['deadline']<=now:raise ValueError('project lease deadline elapsed')



def verify_operator_configuration(operator):
    overrides=('CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT','CLOUDSDK_AUTH_ACCESS_TOKEN',
               'CLOUDSDK_AUTH_ACCESS_TOKEN_FILE','ACCESS_TOKEN_FILE','CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE')
    if any(operator.env.get(key) for key in overrides):
        raise ValueError('operator credential or impersonation override refused')
    for key in ('auth/impersonate_service_account','auth/access_token_file','auth/credential_file_override'):
        value=operator.call('config','get-value',key).strip()
        if value and value!='(unset)':raise ValueError('resolved operator credential override refused')


def verify_operator_identity(api,account):
    result=api('POST','/apis/authentication.k8s.io/v1/selfsubjectreviews',
               {'apiVersion':'authentication.k8s.io/v1','kind':'SelfSubjectReview'})
    if result.get('status',{}).get('userInfo',{}).get('username')!=account:
        raise ValueError('authenticated operator identity differs from requested account')
    return account


def binding_document(args,account):
    return {'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding',
            'metadata':{'name':BINDING,'labels':{'evaluation.karthos.dev/owner':OWNER,'evaluation.karthos.dev/run-id':args.run_id},
                        'annotations':{'evaluation.karthos.dev/cluster-uid':args.cluster_uid}},
            'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':'cluster-admin'},
            'subjects':[{'apiGroup':'rbac.authorization.k8s.io','kind':'User','name':account}]}


def _binding_matches(actual,expected):
    meta=actual.get('metadata',{});wanted=expected['metadata']
    if (actual.get('kind')!='ClusterRoleBinding' or actual.get('apiVersion')!=expected['apiVersion']
            or meta.get('name')!=BINDING or meta.get('deletionTimestamp') or not meta.get('uid')
            or actual.get('roleRef')!=expected['roleRef'] or actual.get('subjects')!=expected['subjects']
            or any(meta.get(k,{}).get(n)!=v for k in ('labels','annotations') for n,v in wanted[k].items())):
        raise ValueError('existing bootstrap binding conflicts with reviewed identity or cluster scope')
    return meta['uid']


def ensure_binding(api,expected,recheck):
    try:existing=api('GET',RBAC+'/'+BINDING)
    except APIError as exc:
        if exc.status!=404:raise
        existing=None
    if existing is not None:
        uid=_binding_matches(existing,expected)
        recheck()
    else:
        recheck()  # Exact UID, ownership, private endpoint and lease immediately before POST.
        created=api('POST',RBAC,expected);uid=_binding_matches(created,expected)
    observed=api('GET',RBAC+'/'+BINDING)
    if _binding_matches(observed,expected)!=uid:raise ValueError('bootstrap binding UID changed during readback')
    return {'binding_name':BINDING,'binding_uid':uid,'created':existing is None,'verified':True}


class SSHForward:
    """One local loopback forward, always terminate its whole process group."""
    def __init__(self,operator,endpoint,*,startup_seconds=45):
        self.operator=operator;self.endpoint=endpoint;self.startup_seconds=startup_seconds
        self.process=None;self.closed=False;self.port=None
    def __enter__(self):
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1',0));self.port=reserved.getsockname()[1]
        a=self.operator.args
        command=['gcloud','--project='+a.project,'compute','ssh',a.controller,'--zone='+a.zone,
                 '--tunnel-through-iap','--quiet','--','-N','-T','-L',f'127.0.0.1:{self.port}:{self.endpoint}:443',
                 '-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1',
                 '-o','BatchMode=yes','-o','ControlMaster=no','-o','ControlPath=none',
                 '-o','ServerAliveInterval=10','-o','ServerAliveCountMax=3']
        end=time.monotonic()+min(self.startup_seconds,self.operator.remaining())
        self.process=subprocess.Popen(command,env=self.operator.env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL,start_new_session=True)
        try:
            while time.monotonic()<end:
                if self.process.poll() is not None:raise RuntimeError('IAP tunnel exited before forwarding became ready')
                try:
                    with socket.create_connection(('127.0.0.1',self.port),timeout=.25):return self
                except OSError:time.sleep(.1)
            raise TimeoutError('IAP tunnel startup deadline elapsed')
        except BaseException:
            self.__exit__(None,None,None);raise
    def __exit__(self,*exc):
        if self.process is None:self.closed=True;return
        try:os.killpg(self.process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            self.process.wait(timeout=5)
        # gcloud may have exited before its SSH/IAP children. Kill that same group.
        try:os.killpg(self.process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        end=time.monotonic()+3
        while time.monotonic()<end:
            try:
                with socket.create_connection(('127.0.0.1',self.port),timeout=.1):pass
            except OSError:self.closed=True;return
            time.sleep(.1)
        raise RuntimeError('local tunnel port remains open after cleanup')


class PrivateAPI:
    def __init__(self,endpoint,ca,port,token,*,remaining=lambda:30):
        self.endpoint=endpoint;self.port=port;self.token=token;self.remaining=remaining
        self.context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try:self.context.load_verify_locations(cadata=base64.b64decode(ca,validate=True).decode('ascii'))
        except (ValueError,UnicodeError,ssl.SSLError):raise ValueError('invalid observed cluster CA') from None
    def __call__(self,method,path,body=None):
        remaining=min(30,self.remaining())
        if remaining<=0:raise TimeoutError('operator bootstrap work deadline elapsed')
        connection=http.client.HTTPSConnection(self.endpoint,timeout=remaining,context=self.context)
        try:
            transport=socket.create_connection(('127.0.0.1',self.port),timeout=remaining)
            try:connection.sock=self.context.wrap_socket(transport,server_hostname=self.endpoint)
            except BaseException:transport.close();raise
            remaining=min(30,self.remaining())
            if remaining<=0:raise TimeoutError('operator bootstrap work deadline elapsed')
            connection.sock.settimeout(remaining)
            connection.request(method,path,body=None if body is None else json.dumps(body),
                               headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            remaining=min(30,self.remaining())
            if remaining<=0:raise TimeoutError('operator bootstrap work deadline elapsed')
            connection.sock.settimeout(remaining)
            response=connection.getresponse();raw=response.read(1024*1024+1)
            if self.remaining()<=0:raise TimeoutError('operator bootstrap work deadline elapsed')
            if response.status>=300:raise APIError(response.status)
            if len(raw)>1024*1024:raise ValueError('Kubernetes bootstrap response exceeds bound')
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError('Kubernetes bootstrap response must be object')
            return value
        finally:connection.close()


class BootstrapOperator(Operator):
    def __init__(self,root,args,**kwargs):
        self.work_expires=time.monotonic()+300;self.cleanup_cutoff=None
        super().__init__(root,args,**kwargs)
        for value in (args.project,args.zone,args.controller,args.run_id,args.cluster):identifier(value)
        if args.owner_id!=OWNER or not args.uid or not args.cluster_uid:raise ValueError('explicit owned target UIDs required')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+',args.operator_account):raise ValueError('explicit operator account required')
        self.env['CLOUDSDK_CORE_ACCOUNT']=args.operator_account
        verify_operator_configuration(self)
        data=Path(os.environ.get('GKE_EVAL_DATA_ROOT',root));path=data/'.work/gke'/args.run_id/'execution.json'
        if not path.is_file() or path.is_symlink():raise ValueError('existing private run inventory required')
        self.state=json.loads(path.read_text());self.config=self.state['cleanup_config']
        if canonical_sha(self.config)!=self.state.get('cleanup_config_sha256'):raise ValueError('saved configuration fingerprint mismatch')
        if self.state.get('run_id')!=args.run_id or self.state.get('owner_id')!=OWNER or self.config['gcp']['project_id']!=args.project or self.config['gcp']['zone']!=args.zone:
            raise ValueError('saved run ownership/project mismatch')
        expected=self.config['gcp'].get('operator_principal')
        if expected and expected!=args.operator_account:raise ValueError('operator account differs from reviewed run')
        self.cleanup_cutoff=self.state['deadline']-self.config['limits']['cleanup_reserve_seconds']
        self.remaining()
        self.foundation=self.state['terraform']['foundation'];self.account=self.foundation['controller_service_account']
        self.creation_proof=None
    def remaining(self):
        remaining=self.work_expires-time.monotonic()
        if self.cleanup_cutoff is not None:remaining=min(remaining,self.cleanup_cutoff-time.time())
        if remaining<=0:raise TimeoutError('operator bootstrap work deadline elapsed')
        return remaining
    def call(self,*args):
        # Recovery's exact-ownership firewall cleanup remains possible after work expiry.
        cleanup=args[:2]==('compute','firewall-rules') and len(args)>2 and args[2] in ('list','describe','delete')
        timeout=90 if cleanup else min(90,self.remaining())
        result=self.executor(['gcloud','--project='+self.args.project,*args],capture_output=True,text=True,timeout=timeout,env=self.env)
        if result.returncode:raise RuntimeError(sanitized_error(result.stderr))
        return result.stdout
    def _read(self,uri,generation=None):
        meta=json.loads(self.call('storage','objects','describe',uri,'--format=json'));actual=str(meta.get('generation',''))
        if not actual.isdigit() or generation is not None and actual!=str(generation):raise ValueError('pinned object generation changed')
        raw=self.call('storage','cat',uri+'#'+actual)
        if len(raw.encode())>4*1024*1024:raise ValueError('bootstrap control object exceeds bound')
        named_hash=re.search(r'/inventory/([a-f0-9]{64})\.json$',uri)
        if named_hash and hashlib.sha256(raw.encode()).hexdigest()!=named_hash.group(1):raise ValueError('inventory content hash differs from pinned URI')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('bootstrap control object must be object')
        return value,actual
    def check(self):
        a=self.args;saved=self.state['lease'];bucket=self.foundation['state_bucket']
        if saved.get('bucket')!=bucket or saved.get('object')!='control/active-run.json' or saved.get('uri')!='gs://'+bucket+'/control/active-run.json':
            raise ValueError('saved project lease target mismatch')
        lease,generation=self._read(saved['uri']);validate_lease(a,saved,lease,generation,time.time())
        reserve=self.config['limits']['cleanup_reserve_seconds']
        if time.time()+120+reserve>=min(self.state['deadline'],lease['deadline']):raise ValueError('insufficient bootstrap time before cleanup cutoff')
        prefix='gs://'+self.foundation['evidence_bucket']+'/control/'+a.run_id+'/'
        if not re.fullmatch(re.escape(prefix)+r'inventory/[a-f0-9]{64}\.json',a.inventory_uri):raise ValueError('current-run pinned inventory URI required')
        inventory,inv_generation=self._read(a.inventory_uri,a.inventory_generation)
        if (inventory.get('project_id')!=a.project or inventory.get('owner_id')!=OWNER or inventory.get('run_id')!=a.run_id
                or inventory.get('lease',{}).get('token')!=saved['token'] or inventory.get('deadline')!=self.state['deadline']):
            raise ValueError('inventory belongs to another run, lease or deadline')
        vm_name=f'projects/{a.project}/zones/{a.zone}/instances/{a.controller}'
        controllers=[r for r in inventory.get('resources',[]) if r.get('kind')=='instance' and r.get('name')==vm_name]
        if len(controllers)!=1 or str(controllers[0].get('uid'))!=a.uid:raise ValueError('inventory does not bind requested controller UID')
        name=f'projects/{a.project}/locations/{a.zone}/clusters/{a.cluster}'
        matches=[r for r in inventory.get('resources',[]) if r.get('kind')=='cluster' and r.get('name')==name]
        if len(matches)!=1 or str(matches[0].get('uid'))!=a.cluster_uid:raise ValueError('inventory does not bind requested cluster UID')
        bound=matches[0].get('binding',{});uri=bound.get('uri','')
        if not re.fullmatch(re.escape(prefix)+r'uid-bindings/[a-f0-9]{32}\.json',uri):raise ValueError('cluster creation UID binding required')
        if not str(bound.get('generation','')).isdigit():raise ValueError('pinned creation binding generation required')
        creation,created_generation=self._read(uri,bound['generation'])
        if creation.get('intent_id')!=uri.rsplit('/',1)[1][:-5]:raise ValueError('creation intent identifier mismatch')
        expected={'kind':'cluster','name':name,'uid':a.cluster_uid,'project_id':a.project,'owner_id':OWNER,'run_id':a.run_id,'lease_token':saved['token']}
        if any(str(creation.get(k))!=str(v) for k,v in expected.items()):raise ValueError('cluster creation binding differs from requested UID or lease')
        cluster=json.loads(self.call('container','clusters','describe',a.cluster,'--zone='+a.zone,'--format=json'))
        vm=self.instance();target=validate_target(a,cluster,vm,self.account)
        # Control-object/cloud reads may be slow. Recheck lease and reserve last.
        final_lease,final_generation=self._read(saved['uri'])
        validate_lease(a,saved,final_lease,final_generation,time.time())
        if time.time()+120+reserve>=min(self.state['deadline'],final_lease['deadline']):raise ValueError('insufficient bootstrap time before cleanup cutoff')
        self.creation_proof={'inventory_uri':a.inventory_uri,'inventory_generation':inv_generation,'binding_uri':uri,'binding_generation':created_generation}
        return target


def bootstrap(root,args,*,operator_factory=BootstrapOperator,tunnel_factory=SSHForward,api_factory=PrivateAPI):
    if args.receipt.exists():raise ValueError('bootstrap receipt already exists')
    op=operator_factory(root,args);target=op.check();forward=None
    def probe(operator):
        nonlocal forward
        if operator.check()!=target:raise ValueError('cluster endpoint or CA changed before tunnel startup')
        token=operator.call('auth','print-access-token','--account='+args.operator_account).strip()
        if not token or '\n' in token:raise ValueError('one operator access token required')
        forward=tunnel_factory(operator,target[0])
        try:
            with forward:
                api=api_factory(target[0],target[1],forward.port,token,remaining=operator.remaining)
                observed_operator=verify_operator_identity(api,args.operator_account)
                def recheck():
                    if operator.check()!=target:raise ValueError('cluster endpoint or CA changed before binding mutation')
                result=ensure_binding(api,binding_document(args,operator.account),recheck)
        finally:token=None
        return dict(result,cluster=args.cluster,cluster_uid=args.cluster_uid,controller_service_account=operator.account,
                    operator_account=args.operator_account,observed_operator=observed_operator,creation_proof=operator.creation_proof,
                    tunnel_removed=forward.closed,scope='cluster-admin only within this UID-verified disposable evaluation cluster')
    result=recover(op,probe,args.receipt)
    result['verified']=bool(result.get('temporary_removal_confirmed') and result.get('observation',{}).get('verified')
                            and result.get('observation',{}).get('tunnel_removed'))
    result['run_id']=args.run_id;result['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    args.receipt.write_text(json.dumps(result,indent=2)+'\n')
    return result
