"""Explicit experiment target, reproducible inputs and bounded execution."""
from __future__ import annotations
import contextlib,fcntl,json,os,subprocess,time
from pathlib import Path
from .models import read,write,sha,implementation_hashes,classify_readiness,identifier,validate_schedule

def ensure_budget(deadline,*,now=None,reserve=0):
    now=time.time() if now is None else now
    if now+reserve>=deadline:raise TimeoutError('infrastructure deadline/reserved cleanup budget reached')
    return deadline-now-reserve

def check_manifest(root,manifest_path,expected_sha=None):
    root=Path(root);manifest_path=Path(manifest_path);manifest=read(manifest_path)
    digest=sha(manifest_path)
    if expected_sha and expected_sha!=digest:raise ValueError('manifest file hash mismatch')
    actual=implementation_hashes(root)
    if actual!=manifest['implementation_hashes']:raise ValueError('prepared source changed; rerun prepare before executing')
    return manifest

class LiveContext:
    def __init__(self,root,manifest_path,*,executor=subprocess.run,run_id=None,allow_pending=False):
        self.root=Path(root).resolve();self.manifest_path=Path(manifest_path).resolve();self.executor=executor
        self.manifest=check_manifest(self.root,self.manifest_path)
        if 'matrix' in self.manifest or 'schedule' in self.manifest:
            validate_schedule(self.manifest)
        issues=classify_readiness(self.manifest['config'],self.manifest['artifact_checks'])
        issues+=self.manifest.get('pending_prerequisites',[])
        if issues and not allow_pending:raise ValueError('execution readiness incomplete: '+'; '.join(issues))
        self.config=self.manifest['config'];self.run_id=identifier(run_id or self.manifest['id'])
        self.data_root=Path(os.environ.get('GKE_EVAL_DATA_ROOT',self.root)).resolve()
        self.work=self.data_root/'.work/gke'/self.run_id;self.work.mkdir(parents=True,exist_ok=True);os.chmod(self.work,0o700)
        self.result=self.data_root/'results/gke-qualification-v1/runs'/self.run_id
        self.result.mkdir(parents=True,exist_ok=True)
        state=self.work/'execution.json';started=time.time()
        self.state=read(state) if state.exists() else {'run_id':self.run_id,'owner_id':'gke-qualification-v1','started_at':started,'deadline':started+self.config['limits']['infrastructure_seconds'],'resources':[]}
        bootstrap=os.environ.get('GKE_EVAL_BOOTSTRAP_CONTEXT')
        if bootstrap and not state.exists():
            data=read(bootstrap)
            if data.get('run_id')!=self.run_id or data.get('project_id')!=self.config['gcp']['project_id'] or data.get('owner_id')!='gke-qualification-v1':raise ValueError('controller bootstrap target mismatch')
            if data.get('deadline',0)>data.get('started_at',0)+43200:raise ValueError('bootstrap extends infrastructure deadline')
            self.state.update({k:data[k] for k in ['owner_id','started_at','deadline','terraform','resources','inventory','watchdog_armed','phase','manifest_object','scheduler_name','cluster_generation','ever_watchdog_armed','project_number','cleanup_config','cleanup_config_sha256','cleanup_target','cleanup_target_sha256','lease'] if k in data})
        if not state.exists():write(state,self.state)
        self.kubeconfig=self.work/'kubeconfig';self.deadline=self.state['deadline'];self._lock=None
    def command(self,args,*,timeout=600,input_text=None,check=True,binary=False,cleanup=False,cwd=None,**ignored):
        deadline = getattr(self, 'phase_deadline', self.deadline)
        reserve = getattr(self, 'phase_reserve', self.config['limits']['cleanup_reserve_seconds'])
        if self.manifest.get('readiness_check') and not self.state.get('readiness_handoff_at'):
            from .readiness import RESERVE, cutoff
            deadline, reserve = min(deadline, cutoff(self)), RESERVE
        remaining=timeout if cleanup else ensure_budget(deadline,reserve=reserve)
        from .cloud_identity import command_environment,command_failure
        result=self.executor([str(x) for x in args],env=command_environment(self.root),cwd=cwd,input=input_text.encode() if binary and isinstance(input_text,str) else input_text,text=not binary,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=min(timeout,remaining))
        if check and result.returncode:raise command_failure(self,args,result)
        return result
    def kube(self,*args,**kwargs):return self.command(['kubectl','--kubeconfig',str(self.kubeconfig),*args],**kwargs)
    def json(self,*args):return json.loads(self.kube(*args,'-o','json').stdout)
    def write(self,path,value):write(path,value)
    def event(self,phase,**fields):
        self.state.update(phase=phase,**fields);write(self.work/'execution.json',self.state)
        with (self.result/'progress.ndjson').open('a') as out:out.write(json.dumps({'at':time.time(),'phase':phase,**fields})+'\n')
    @contextlib.contextmanager
    def controller_lock(self):
        p=self.work/'controller.lock'
        with p.open('a+') as f:
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('another controller holds the run lock')
            f.seek(0);f.truncate();f.write(json.dumps({'pid':os.getpid(),'run_id':self.run_id}));f.flush()
            try:yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)
