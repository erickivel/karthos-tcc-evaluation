"""Explicit per-run quarantine; no permanent bucket-wide controller grants."""
from __future__ import annotations
import json,re,tempfile
from pathlib import Path
from .models import sha

PREFIX = 'gke-quarantine-v1/runs/'


def _upload_original(ctx,path,bucket,name):
    """Exact immutable JSON-API upload; SDK destination discovery needs bucket list.

    Stream the existing file with curl; pass the bearer header on stdin, never
    in process arguments or retained diagnostics. The controller gcloud context
    supplies the same identity used for metadata and checksum readback.
    """
    from urllib.parse import urlencode,quote
    token=ctx.command(['gcloud','auth','print-access-token'],timeout=60,cleanup=True).stdout.strip()
    if not re.fullmatch(r'[A-Za-z0-9._~+/=-]+',token):raise ValueError('invalid quarantine access-token response')
    url='https://storage.googleapis.com/upload/storage/v1/b/'+quote(bucket,safe='')+'/o?'+urlencode({'uploadType':'media','name':name,'ifGenerationMatch':'0'})
    result=ctx.command(['curl','--disable','--silent','--show-error','--fail-with-body','--proto','=https',
        '--connect-timeout','20','--max-time','290','--request','POST','--header','@-',
        '--upload-file',str(path),'--write-out','\n%{http_code}',url],
        input_text='Authorization: Bearer '+token+'\nContent-Type: application/octet-stream\n',
        timeout=300,cleanup=True,check=False)
    status=result.stdout.rsplit('\n',1)[-1].strip()
    # A raced/uncertain previous create is accepted only by the unchanged,
    # generation-pinned full checksum readback in transfer().
    if not (result.returncode==0 and status in ('200','201')) and status!='412':
        from .cloud_identity import command_failure
        error=command_failure(ctx,['curl','quarantine-upload'],result)
        raise RuntimeError('quarantine exact-object upload failed: HTTP '+status+'; '+str(error))


def from_context(ctx):
    config = ctx.config.get('quarantine')
    if config is None:
        return None
    bucket = config.get('bucket', '')
    prefix = config.get('prefix', '')
    foundation = ctx.state['terraform']['foundation']
    if (not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]', bucket)
            or bucket != foundation.get('quarantine_bucket')
            or bucket == foundation.get('evidence_bucket')
            or prefix != PREFIX + ctx.run_id + '/'):
        raise ValueError('quarantine bucket/prefix does not match configured current run')
    def transfer(path, metadata):
        name = metadata['original_relative_path']
        if Path(name).is_absolute() or '..' in Path(name).parts or name.splitlines() != [name]:
            raise ValueError('unsafe quarantine object path')
        # Hash names avoid collisions across logical windows without disclosing
        # original file names to bucket listing readers.
        import hashlib
        key = hashlib.sha256(name.encode()).hexdigest() + '/' + metadata['sha256']
        uri = 'gs://' + bucket + '/' + prefix + key
        if path.is_symlink() or sha(path) != metadata['sha256'] or path.stat().st_size != metadata['bytes']:
            raise ValueError('quarantine original changed before upload')
        exists = ctx.command(['gcloud','storage','objects','describe',uri,'--format=json'],timeout=60,cleanup=True,check=False)
        if exists.returncode:
            if not any(x in str(exists.stderr) for x in ('404','NotFound','not found','not exist')):
                raise RuntimeError('quarantine object lookup denied or failed')
            _upload_original(ctx,path,bucket,prefix+key)
        result = ctx.command(['gcloud','storage','objects','describe',uri,'--format=json'],timeout=60,cleanup=True)
        generation = str(json.loads(result.stdout)['generation'])
        if not re.fullmatch(r'[1-9][0-9]*', generation):
            raise ValueError('invalid quarantine generation')
        with tempfile.TemporaryDirectory(prefix='gke-quarantine-readback-') as temporary:
            local = Path(temporary)/'object'
            ctx.command(['gcloud','storage','cp',uri+'#'+generation,str(local)],timeout=300,cleanup=True)
            if sha(local) != metadata['sha256'] or local.stat().st_size != metadata['bytes']:
                raise ValueError('quarantine readback checksum mismatch')
        if sha(path) != metadata['sha256']:
            raise ValueError('quarantine original changed during upload')
        return dict(metadata,uri=uri,generation=generation,verified=True)
    return transfer


def iam_proposal(bucket, run_id, member):
    """Reviewable launch-scoped bindings; this helper never mutates IAM."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]', bucket) or not re.fullmatch(r'[A-Za-z0-9_-]{1,220}',run_id):
        raise ValueError('invalid quarantine IAM scope')
    if not re.fullmatch(r'serviceAccount:[A-Za-z0-9_.@-]+',member):
        raise ValueError('invalid quarantine IAM member')
    condition = {'title':'QuarantineCurrentRun', 'expression':"resource.name.startsWith('projects/_/buckets/"+bucket+"/objects/"+PREFIX+run_id+"/')"}
    return [{'bucket':bucket,'role':role,'member':member,'condition':condition} for role in ('roles/storage.objectCreator','roles/storage.objectViewer')]


def preflight_requirements(ctx):
    """Return launch review inputs, never pretend that IAM was tested remotely.

    Prefix-conditional object create/get is exercised by the real immutable
    upload/readback. This proposal does not authorize traffic or provision IAM.
    """
    if from_context(ctx) is None:
        return None
    config = ctx.config['quarantine']
    foundation = ctx.state['terraform']['foundation']
    member = 'serviceAccount:' + foundation['controller_service_account']
    return {'bucket':config['bucket'], 'prefix':config['prefix'],
            'required_permissions':['storage.objects.create','storage.objects.get'],
            'iam_bindings':iam_proposal(config['bucket'], ctx.run_id, member),
            'expected_bucket_policy':{'retention_seconds':7776000, 'retention_locked':False,
                'versioning_enabled':True, 'public_access_prevention':'enforced',
                'uniform_bucket_level_access':True},
            'remote_permissions_verified':False,
            'scope':'review-only; apply/remove launch-scoped bindings explicitly'}


def probe_access(ctx, directory):
    """Small immutable access check under the actual running controller identity."""
    transfer=from_context(ctx)
    if transfer is None:
        raise ValueError('quarantine configuration required for v2 access probe')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/'quarantine-access-probe.json'
    content=json.dumps({'run_id':ctx.run_id,'purpose':'non-sensitive quarantine permission probe'},sort_keys=True)+'\n'
    if path.exists() and path.read_text()!=content:
        raise ValueError('quarantine probe identity changed')
    if not path.exists():
        with path.open('x') as stream:stream.write(content)
    receipt=transfer(path,{'original_relative_path':'quarantine-access-probe.json',
                           'bytes':path.stat().st_size,'sha256':sha(path)})
    return dict(receipt,state='passed',scope='non-sensitive storage permission probe; no workload traffic')


def verify_preprovision_policy(ctx):
    """Check explicit current-run bindings, never mutate IAM or prove access.

    The operator must apply the proposal before provisioning. Exact expressions
    avoid interpreting arbitrary CEL. The real controller upload/readback probe
    remains mandatory: policy presence does not establish effective permissions.
    """
    from .complement_budget import FULL_PROFILE
    from .models import canonical_sha, write
    if ctx.config.get('execution_profile') != FULL_PROFILE:
        return None
    requirements = preflight_requirements(ctx)
    if requirements is None:
        raise ValueError('quarantine configuration required before provisioning')
    result = ctx.command(['gcloud','storage','buckets','get-iam-policy',
                          'gs://' + requirements['bucket'],'--format=json'],timeout=60)
    if result.returncode:
        raise RuntimeError('quarantine bucket IAM policy read failed')
    policy = json.loads(result.stdout)
    bindings = policy.get('bindings') if isinstance(policy,dict) else None
    if not isinstance(bindings,list):
        raise ValueError('quarantine IAM policy bindings missing or malformed')
    for expected in requirements['iam_bindings']:
        if not any(isinstance(binding,dict)
                   and binding.get('role') == expected['role']
                   and isinstance(binding.get('members'),list)
                   and expected['member'] in binding['members']
                   and isinstance(binding.get('condition'),dict)
                   and binding['condition'].get('expression') == expected['condition']['expression']
                   for binding in bindings):
            raise ValueError('quarantine current-run IAM bindings missing; operator must explicitly apply iam_proposal grants before provisioning')
    receipt = {'bucket':requirements['bucket'], 'prefix':requirements['prefix'],
               'run_id':ctx.run_id, 'iam_bindings':requirements['iam_bindings'],
               'policy_sha256':canonical_sha(policy), 'policy_bindings_verified':True,
               'remote_permissions_verified':False,
               'scope':'bucket policy binding check only; actual controller upload/readback probe remains mandatory'}
    write(ctx.work/'quarantine-policy-preflight.json',receipt)
    ctx.state['quarantine_policy_preflight'] = receipt
    return receipt
