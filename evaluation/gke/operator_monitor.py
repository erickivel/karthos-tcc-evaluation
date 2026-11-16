"""Bounded, external observations; never changes execution state or declares completion."""
from __future__ import annotations
import datetime,json,subprocess,time,shlex
from .cloud_identity import command_environment,sanitized_error
from .monitor import controller_health


class Operator:
    def __init__(self, root, args, executor=subprocess.run):
        self.args = args
        self.env = command_environment(root)
        self.executor = executor

    def call(self, *args):
        result = self.executor(['gcloud', '--project='+self.args.project, *args],
                               capture_output=True, text=True, timeout=90, env=self.env)
        if result.returncode:
            raise RuntimeError(sanitized_error(result.stderr))
        return result.stdout

    def instance(self):
        value = json.loads(self.call('compute','instances','describe',self.args.controller,
                                    '--zone='+self.args.zone,'--format=json'))
        labels = value.get('labels', {})
        if (str(value.get('id')) != self.args.uid or labels.get('evaluation-owner') != self.args.owner_id
                or labels.get('evaluation-run') != self.args.run_id):
            raise ValueError('controller ownership or UID mismatch')
        return value

    def ssh(self, command):
        return self.call('compute','ssh',self.args.controller,'--zone='+self.args.zone,
                         '--tunnel-through-iap','--quiet','--command='+command,'--','-o','ConnectTimeout=15',
                         '-o','BatchMode=yes')


def observe(operator):
    from .models import identifier
    run_id=identifier(operator.args.run_id)
    errors = {}
    provider, process, serial = None, None, ''
    progress={'state':'unknown','reason':'controller progress not acquired'}
    try:
        provider = operator.instance()
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
        errors['provider'] = sanitized_error(str(exc))
        # Never SSH to a controller whose identity was not verified.
    if provider is not None:
        try:
            serial = operator.call('compute','instances','get-serial-port-output',operator.args.controller,
                                   '--zone='+operator.args.zone,'--start=0')
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            errors['serial'] = sanitized_error(str(exc))
        try:
            process = json.loads(operator.ssh("sudo docker inspect --format '{{json .State}}' karthos-eval-controller"))
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            errors['process'] = sanitized_error(str(exc))
        try:
            command='sudo docker exec karthos-eval-controller python -m evaluation.gke.controller_progress '+shlex.quote(run_id)
            raw=operator.ssh(command)
            if len(raw.encode())>8*1024**2:raise ValueError('progress response exceeds bound')
            progress=json.loads(raw)
            if not isinstance(progress,dict) or progress.get('run_id')!=run_id:raise ValueError('progress run identity mismatch')
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
            errors['progress'] = sanitized_error(str(exc))
            progress={'state':'unknown','reason':'controller progress acquisition failed'}
    now = datetime.datetime.now(datetime.timezone.utc)
    return {'checked_at':now.isoformat(), 'run_id':operator.args.run_id,
            'next_check_due':(now+datetime.timedelta(seconds=operator.args.interval)).isoformat(),
            'controller_health':controller_health(provider,process,serial), 'progress':progress, 'observation_errors':errors,
            'terminal_state':'unknown', 'keep_observing':True,
            'scope':'external provider/container/serial and bounded progress observation; no evidence seal or cleanup verification'}


def monitor(root,args):
    if args.interval < 1: raise ValueError('interval must be positive')
    operator = Operator(root,args)
    while True:
        result = observe(operator)
        if not args.watch: return result
        print(json.dumps(result),flush=True)
        time.sleep(args.interval)
