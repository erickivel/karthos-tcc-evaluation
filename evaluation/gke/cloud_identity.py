"""One explicit subprocess credential context; never change global gcloud config."""
from __future__ import annotations
import json,os,re,time,uuid
from pathlib import Path


def command_environment(root,environ=None):
    env=dict(os.environ if environ is None else environ)
    cloud=env.get('CLOUDSDK_CONFIG') or env.get('GKE_EVAL_CLOUDSDK_CONFIG') or str(Path(root)/'.work/gke/gcloud')
    env['CLOUDSDK_CONFIG']=cloud
    # The bootstrapped controller intentionally obtains ADC from attached VM metadata.
    # A fabricated file path would disable that supported ADC fallback.
    if not env.get('GKE_EVAL_BOOTSTRAP_CONTEXT') or env.get('GKE_EVAL_GOOGLE_APPLICATION_CREDENTIALS'):
        env.setdefault('GOOGLE_APPLICATION_CREDENTIALS',env.get('GKE_EVAL_GOOGLE_APPLICATION_CREDENTIALS') or str(Path(cloud)/'application_default_credentials.json'))
    return env


def sanitized_error(text):
    from .export import redact_text
    text=re.sub(r'(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----','<redacted>',str(text))
    text=re.sub(r'(?i)Bearer\s+[^\s]+','Bearer <redacted>',text)
    return redact_text(text)[-1200:]


def command_failure(ctx,args,result):
    """Keep exact stderr privately, return bounded sanitized command/exit evidence."""
    error=result.stderr.decode(errors='backslashreplace') if isinstance(result.stderr,bytes) else result.stderr or ''
    directory=ctx.work/'command-diagnostics';directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
    name=str(time.time_ns())+'-'+uuid.uuid4().hex[:8]
    raw=directory/(name+'.stderr')
    descriptor=os.open(raw,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'w') as output:output.write(error)
    stage=' '.join(str(x) for x in args[:3] if not str(x).startswith('-'))
    return RuntimeError(stage+' exited '+str(result.returncode)+': '+sanitized_error(error)+' [private diagnostic '+name+']')


def principal_preflight(ctx):
    """Read-only observed CLI principal, ADC identity and selected project."""
    env=command_environment(ctx.root)
    active=ctx.command(['gcloud','auth','list','--filter=status:ACTIVE','--format=value(account)']).stdout.strip()
    impersonated=ctx.command(['gcloud','config','get-value','auth/impersonate_service_account'],check=False).stdout.strip()
    principal=impersonated if impersonated and impersonated!='(unset)' else active
    if not principal or '\n' in principal:raise ValueError('one active gcloud principal required in isolated context')
    adc=Path(env['GOOGLE_APPLICATION_CREDENTIALS'])
    if not adc.is_file():raise ValueError('explicit ADC file missing in operator credential context')
    data=json.loads(adc.read_text());adc_principal=data.get('client_email')
    if data.get('type')=='service_account' and adc_principal!=principal:
        raise ValueError('Terraform ADC and gcloud principal differ')
    if data.get('type')=='authorized_user' and not data.get('refresh_token'):
        raise ValueError('operator ADC authorized-user credentials incomplete')
    expected=ctx.config.get('gcp',{}).get('operator_principal')
    if expected and expected!=principal:raise ValueError('observed principal differs from configured operator')
    return {'principal':principal,'project_id':ctx.config['gcp']['project_id'],
            'adc_type':data.get('type'),'adc_principal_verified':bool(adc_principal and adc_principal==principal),
            'credential_context':'explicit subprocess environment; user ADC identity may require operator verification','read_only':True}
