import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from evaluation.generator_artifacts import validate_export, download_export


def bundle(path, exit_code=0):
    path.mkdir(exist_ok=True)
    start='2026-10-20T00:00:00Z';end='2026-10-20T00:00:05Z'
    events=[('WINDOW',dict(run_id='run',started_at=start,ended_at=None)),
            ('ATTEMPT',dict(run_id='run',request_id='a')),
            ('REQUEST',dict(run_id='run',request_id='a')),
            ('WINDOW',dict(run_id='run',started_at=start,ended_at=end))]
    files={'generator.log': ''.join('EVAL_'+k+' '+json.dumps(v)+'\n' for k,v in events),
           'generator-version.txt':'k6 pinned\n','run-id.txt':'run\n',
           'workload-started-at.txt':start,'workload-ended-at.txt':end,
           'workload-exit-code.txt':str(exit_code),
           'k6-summary.json':json.dumps({'metrics':{'http_reqs':{'values':{'count':1}},
                                                   'dropped_iterations':{'values':{'count':0}}}}),
           'k6.ndjson':json.dumps({'type':'Point','metric':'http_reqs','data':{'value':1}})+'\n'}
    for name,value in files.items():(path/name).write_text(value)
    rehash(path)


def rehash(path):
    (path/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
        for p in sorted(path.iterdir()) if p.is_file() and p.name!='SHA256SUMS'))


class GeneratorArtifactTests(unittest.TestCase):
    def test_positive_dropped_work_requires_matching_granular_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bundle(p,99)
            summary=json.loads((p/'k6-summary.json').read_text())
            summary['metrics']['dropped_iterations']['values']['count']=1
            (p/'k6-summary.json').write_text(json.dumps(summary));rehash(p)
            with self.assertRaises(ValueError):validate_export(p,'k6','matched','run')
            with (p/'k6.ndjson').open('a') as out:
                out.write(json.dumps({'type':'Point','metric':'dropped_iterations','data':{'value':1}})+'\n')
            rehash(p)
            self.assertTrue(validate_export(p,'k6','matched','run')['verified'])

    def test_locust_native_count_must_reconcile_with_matched_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bundle(p)
            for file in ('k6.ndjson','k6-summary.json'):(p/file).unlink()
            for name in ('locust-failures.csv','locust-exceptions.csv','locust-stats_history.csv'):
                (p/name).write_text('header\n')
            (p/'locust-report.html').write_text('<html>report</html>')
            stats=p/'locust-stats.csv'
            stats.write_text('Name,Request Count,Failure Count\nAggregated,100,0\n');rehash(p)
            with self.assertRaises(ValueError):validate_export(p,'locust','matched','run')
            stats.write_text('Name,Request Count,Failure Count\nAggregated,1,0\n');rehash(p)
            self.assertTrue(validate_export(p,'locust','matched','run')['semantic_complete'])

    def test_native_ledger_requires_reconciliation_before_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bundle(p)
            for file in ('k6.ndjson','k6-summary.json'):(p/file).unlink()
            for name in ('locust-failures.csv','locust-exceptions.csv','locust-stats_history.csv'):
                (p/name).write_text('header\n')
            (p/'locust-report.html').write_text('<html>native report</html>')
            (p/'locust-stats.csv').write_text('Name,Request Count,Failure Count\nAggregated,2,0\n')
            rehash(p)
            with self.assertRaisesRegex(ValueError,'ledger'):
                validate_export(p,'locust','native-ledger','run')
            (p/'locust-stats.csv').write_text('Name,Request Count,Failure Count\nAggregated,1,0\n')
            rehash(p)
            self.assertEqual(validate_export(p,'locust','native-ledger','run')['requests'],1)
            (p/'generator.log').write_text('no native ledger\n');rehash(p)
            with self.assertRaisesRegex(ValueError,'window marker'):
                validate_export(p,'locust','native-ledger','run')

    def test_shutdown_aborts_and_native_failure_counts_are_accounted_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bundle(p,1)
            for file in ('k6.ndjson','k6-summary.json'):(p/file).unlink()
            for name in ('locust-failures.csv','locust-exceptions.csv','locust-stats_history.csv'):
                (p/name).write_text('header\n')
            (p/'locust-report.html').write_text('<html>report</html>')
            events=[json.loads(l.split(' ',1)[1]) for l in (p/'generator.log').read_text().splitlines()]
            events[2].update(native_reported=True,native_failure=True)
            log=(p/'generator.log').read_text().splitlines()
            log[2]='EVAL_REQUEST '+json.dumps(events[2])
            log.insert(3,'EVAL_ATTEMPT '+json.dumps(dict(run_id='run',request_id='aborted')))
            log.insert(4,'EVAL_REQUEST '+json.dumps(dict(run_id='run',request_id='aborted',native_reported=False,native_failure=False)))
            (p/'generator.log').write_text('\n'.join(log)+'\n')
            (p/'locust-stats.csv').write_text('Name,Request Count,Failure Count\nAggregated,1,0\n');rehash(p)
            with self.assertRaisesRegex(ValueError,'failure'):
                validate_export(p,'locust','native-ledger','run')
            (p/'locust-stats.csv').write_text('Name,Request Count,Failure Count\nAggregated,1,1\n');rehash(p)
            result=validate_export(p,'locust','native-ledger','run')
            self.assertEqual(result['requests'],2)
            self.assertEqual(result['native_reported_requests'],1)
            self.assertEqual(result['not_reported_requests'],1)
            self.assertEqual(result['native_failures'],1)

    def test_valid_workload_failure_remains_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bundle(p,99)
            result=validate_export(p,'k6','matched','run')
            self.assertTrue(result['verified']);self.assertTrue(result['semantic_complete'])
            self.assertEqual(result['workload_exit_code'],99)

    def test_hash_missing_tail_wrong_run_and_summary_disagreement_are_rejected(self):
        changes=[('generator.log','corrupt',False),
                 ('generator.log','EVAL_WINDOW {"run_id":"run","ended_at":null}\n',True),
                 ('run-id.txt','different',True),
                 ('k6-summary.json','{"metrics":{"http_reqs":{"values":{"count":2}}}}',True)]
        for name,value,hash_again in changes:
            with self.subTest(name=name,value=value),tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp);bundle(p);(p/name).write_text(value)
                if hash_again:rehash(p)
                with self.assertRaises(ValueError):validate_export(p,'k6','matched','run')

    def test_unlisted_required_file_and_unsafe_manifest_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);bundle(p)
            manifest=p/'SHA256SUMS'
            manifest.write_text('\n'.join(x for x in manifest.read_text().splitlines() if 'k6-summary.json' not in x)+'\n')
            with self.assertRaises(ValueError):validate_export(p,'k6','matched','run')
            manifest.write_text('a'*64+'  ../outside\n')
            with self.assertRaises(ValueError):validate_export(p,'k6','matched','run')

    def test_transfer_retries_corruption_and_preserves_attempt_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);remote=root/'remote';bundle(remote);calls=[]
            def fetch(name,destination,timeout):
                calls.append(name)
                destination.write_bytes(b'truncated' if name=='generator.log' and calls.count(name)==1
                                        else (remote/name).read_bytes())
            result=download_export('ns','pod','load',root/'out','k6','matched','run',fetch=fetch)
            self.assertTrue(result['verified']);self.assertEqual(calls.count('generator.log'),2)
            records=[json.loads(x) for x in (root/'out/transfer-attempts.ndjson').read_text().splitlines()]
            self.assertTrue(any(x.get('state')=='failed' for x in records))

    def test_failed_transfer_never_acknowledges_or_deletes_pod(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fetch(name,destination,timeout):raise subprocess.TimeoutExpired('exec',timeout)
            with self.assertRaises(ValueError):
                download_export('ns','pod','load',Path(tmp),'k6','matched','run',fetch=fetch)
            self.assertTrue((Path(tmp)/'transfer-attempts.ndjson').exists())
