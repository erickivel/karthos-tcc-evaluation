"""Bounded live log-capture diagnostic on the retained, owned Astronomy cell.

KUBECONFIG=<Kivel config> python3 -m evaluation.live_capture_probe <new-output-dir>
This is a capture integration check, not a scored study window.
"""
import json
import sys
import time
from pathlib import Path

from . import runner as r
from .capture import ContinuousCapture
from .log_capture import PollingLogCapture
from .manifests import k6_job, OWNER_LABEL, OWNER_VALUE
from .pilot import loggable_pod, extract_events, generator_log_complete
from .protocol import Cell


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    cell = Cell('astronomy', 'trace', 1)
    namespace = r.kube_json('get', 'namespace', cell.namespace)
    if namespace['metadata'].get('labels', {}).get(OWNER_LABEL) != OWNER_VALUE:
        raise RuntimeError('capture probe requires the evaluation-owned Astronomy namespace')
    r._DEADLINE = time.monotonic() + 240
    capture = ContinuousCapture(cell, output).start()
    reader = None
    job = k6_job(cell.demo, cell.source, output.name, cell.namespace,
                 r.CONFIG['k6_image'], 'http://frontend-proxy:8080',
                 duration_seconds=20, rate=1, workload='checkout-heavy')
    name = job['metadata']['name']
    container = job['spec']['template']['spec']['containers'][0]['name']
    job['spec']['activeDeadlineSeconds'] = 90
    r.write(output/'job.json', job)
    try:
        r.apply(job)
        while True:
            pod = loggable_pod(r.kube_json('get', 'pods', '-n', cell.namespace,
                                         '-l', 'job-name='+name), container)
            if pod:
                break
            time.sleep(1)
        reader = PollingLogCapture(cell.namespace, pod, container,
                                  output/'generator-stream.log', capture.started_at,
                                  timestamps=False).start()
        while True:
            job_status = r.kube_json('get', 'job', name, '-n', cell.namespace).get('status', {})
            if job_status.get('succeeded') or job_status.get('failed'):
                break
            time.sleep(2)
        reader.stop()
        complete = r.kube('logs', pod, '-n', cell.namespace, '-c', container).stdout
        r.write(output/'generator-retained.log', complete)
        time.sleep(5)
    finally:
        if reader:
            reader.stop()
            r.write(output/'generator-log-status.json', reader.status())
        capture.stop()
        r.kube('delete', 'job', name, '-n', cell.namespace, '--ignore-not-found', check=False)
    status = json.loads((output/'capture-status.json').read_text())
    polled = (output/'generator-stream.log').read_text()
    # Tool output can include a kubectl trailing newline. Compare logical lines.
    checks = dict(descriptive_only=True, capture_streams=len(status['streams']),
                  generator_job_failed=bool(job_status.get('failed')),
                  generator_exact_lines=polled.splitlines() == complete.splitlines(),
                  generator_valid=generator_log_complete(reader.status()),
                  request_records=len(extract_events(polled, 'REQUEST')),
                  all_streams_valid=all(not x.get('exited_before_stop') and
                                       not x.get('error') and not x.get('errors') and
                                       x.get('returncode') == 0 for x in status['streams']),
                  sampler_stopped=status['sampler_stopped'])
    checks['passed'] = not checks['generator_job_failed'] and all(checks[k] for k in ('generator_exact_lines', 'generator_valid',
                                              'all_streams_valid', 'sampler_stopped')) and checks['request_records'] > 0
    r.write(output/'probe-result.json', checks)
    r.checksums(output)
    print(json.dumps(checks, indent=2))
    if not checks['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
