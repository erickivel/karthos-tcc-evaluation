"""Explicit per-run quarantine; no permanent bucket-wide controller grants."""
from __future__ import annotations
import json,re,tempfile
from pathlib import Path
from .models import sha

PREFIX = 'gke-quarantine-v1/runs/'


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
            ctx.command(['gcloud','storage','cp','--if-generation-match=0',str(path),uri],timeout=300,cleanup=True)
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
