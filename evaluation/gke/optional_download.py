"""Optional operator-only evidence downloads; required controller exports are separate."""
from __future__ import annotations
import contextlib,fcntl,hashlib,json,os,re,subprocess,tempfile,time,uuid
from pathlib import Path
from .models import CAPTURE_V2_SCHEDULES,REPAIR_V2,identifier,read,sha
from .cloud_identity import command_environment,sanitized_error

LIMIT=5*1024**3


def _work(root,run_id):
    return Path(os.environ.get('GKE_EVAL_DATA_ROOT',str(root)))/'.work/gke'/identifier(run_id)


@contextlib.contextmanager
def _ledger_lock(work):
    work.mkdir(parents=True,exist_ok=True)
    if work.is_symlink():raise ValueError('optional download ledger directory symlink refused')
    lock=work/'optional-downloads.lock'
    with os.fdopen(os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600),'a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def _load(work,run_id):
    path=work/'optional-downloads.json'
    if path.is_symlink():raise ValueError('optional download ledger symlink refused')
    ledger=read(path) if path.exists() else {'schema_version':1,'run_id':run_id,'limit_bytes':LIMIT,'reservations':[]}
    if ledger.get('schema_version')!=1 or ledger.get('run_id')!=run_id or ledger.get('limit_bytes')!=LIMIT:
        raise ValueError('optional download ledger identity/policy changed')
    rows=ledger.get('reservations')
    if not isinstance(rows,list) or any(type(x.get('bytes')) is not int or x['bytes']<0 for x in rows):
        raise ValueError('invalid optional download reservations')
    if sum(x['bytes'] for x in rows)>LIMIT:raise ValueError('optional downloads exceed5 GiB')
    return ledger


def _save(work,ledger):
    with tempfile.NamedTemporaryFile(mode='w',prefix='.optional-downloads-',dir=work,delete=False) as output:
        name=Path(output.name)
        try:
            json.dump(ledger,output,indent=2,sort_keys=True);output.write('\n');output.flush();os.fsync(output.fileno())
        except BaseException:name.unlink(missing_ok=True);raise
    os.replace(name,work/'optional-downloads.json')


def status(root,run_id):
    work=_work(root,run_id)
    if not (work/'optional-downloads.json').exists():return {'limit_bytes':LIMIT,'reserved_bytes':0,'remaining_bytes':LIMIT,'completed':0,'failed':0,'active':False,'scope':'optional operator object payload downloads only'}
    with _ledger_lock(work):ledger=_load(work,run_id)
    rows=ledger['reservations'];reserved=sum(x['bytes'] for x in rows)
    return {'limit_bytes':LIMIT,'reserved_bytes':reserved,'remaining_bytes':LIMIT-reserved,
            'active':True,'completed':sum(x.get('state')=='complete' for x in rows),'failed':sum(x.get('state')=='failed' for x in rows),
            'scope':'optional operator object payload downloads only; failed/inflight reservations remain charged'}


def download(root,manifest_path,run_id,object_uri,destination,*,executor=subprocess.run):
    """Reserve one known immutable object before download; no recursive or raw reads."""
    if os.environ.get('GKE_EVAL_BOOTSTRAP_CONTEXT'):raise ValueError('optional download is operator-only; controller readbacks remain separate')
    run_id=identifier(run_id);manifest=read(manifest_path);config=manifest['config']
    if manifest.get('schedule') not in CAPTURE_V2_SCHEDULES or manifest.get('id')!=run_id:
        raise ValueError('optional download requires the exact current v2 run manifest')
    limit=config.get('limits',{}).get('optional_local_download_bytes')
    if type(limit) is not int or limit!=LIMIT:raise ValueError('v2 optional downloads require the configured5 GiB cap')
    bucket=config['gcp']['project_id']+'-karthos-eval-evidence'
    prefix='gs://'+bucket+'/gke-qualification-v1/runs/'+run_id+'/'
    if (not isinstance(object_uri,str) or not object_uri.startswith(prefix) or object_uri==prefix
            or any(x in object_uri for x in ('*','?','[',']','#','\n','\r'))
            or any(part in ('.','..','') for part in object_uri[len(prefix):].split('/'))):
        raise ValueError('optional download must name one current-run primary evidence object; raw quarantine is excluded')
    destination=Path(destination)
    if destination.exists() or destination.is_symlink():raise FileExistsError('optional download destination already exists')
    if not destination.parent.is_dir():raise ValueError('optional download destination parent must exist')
    env=command_environment(Path(root))
    # No hidden whole-object retry can consume another unreserved payload.
    env['CLOUDSDK_STORAGE_MAX_RETRIES']='0'
    def command(args):
        value=executor(args,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
        if value.returncode:raise RuntimeError('optional download command failed: '+sanitized_error(value.stderr))
        return value
    metadata=json.loads(command(['gcloud','storage','objects','describe',object_uri,'--format=json']).stdout)
    size=metadata.get('size');generation=str(metadata.get('generation',''))
    if type(size) is int and size>=0:pass
    elif isinstance(size,str) and re.fullmatch(r'[0-9]+',size):size=int(size)
    else:raise ValueError('optional download object size is unknown or invalid')
    if not re.fullmatch(r'[1-9][0-9]*',generation):raise ValueError('optional download object generation is unknown or invalid')
    work=_work(root,run_id);reservation={'id':uuid.uuid4().hex,'object':object_uri,'generation':generation,
        'bytes':size,'state':'reserved','reserved_at':time.time(),'manifest_sha256':sha(manifest_path)}
    with _ledger_lock(work):
        ledger=_load(work,run_id)
        if sum(x['bytes'] for x in ledger['reservations'])+size>limit:raise ValueError('optional download would exceed cumulative5 GiB cap')
        ledger['reservations'].append(reservation);_save(work,ledger)
    try:
        with tempfile.TemporaryDirectory(prefix='.optional-download-',dir=destination.parent) as directory:
            local=Path(directory)/'object'
            command(['gcloud','storage','cp','--do-not-decompress',object_uri+'#'+generation,str(local)])
            if not local.is_file() or local.stat().st_size!=size:raise ValueError('optional download size differs from reserved immutable object')
            reservation.update(state='complete',sha256=sha(local),completed_at=time.time())
            # Atomic no-overwrite publication in the selected destination filesystem.
            os.link(local,destination)
    except BaseException as exc:
        reservation.update(state='failed',error_type=type(exc).__name__,error=sanitized_error(str(exc)))
        raise
    finally:
        with _ledger_lock(work):
            ledger=_load(work,run_id)
            rows=[x for x in ledger['reservations'] if x['id']==reservation['id']]
            if len(rows)!=1:raise ValueError('optional download reservation disappeared')
            rows[0].update(reservation);_save(work,ledger)
    return dict(reservation,destination=str(destination),limit_bytes=limit,receipt=str(work/'optional-downloads.json'))
