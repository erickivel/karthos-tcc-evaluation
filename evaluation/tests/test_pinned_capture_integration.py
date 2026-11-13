"""Opt-in short replay of the actual pinned writer and deployed reader image."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid
from pathlib import Path

from evaluation.capture_finalization import decode_frame, freeze_prefix
from evaluation.collector_tail_stream import CollectorTailStream, reader_script

COLLECTOR='otel/opentelemetry-collector-contrib@sha256:125bdbeb7590cc1952c5b3430ecf14063568980c2c93d5b38676cc0446ed8108'
READER='nginx@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284'


@unittest.skipUnless(os.environ.get('KARTHOS_PINNED_CAPTURE_TEST')=='1','explicit local Docker integration opt-in')
class PinnedCaptureIntegration(unittest.TestCase):
    def test_continuous_collector_appends_are_sealed_by_actual_reader_transport(self):
        token=uuid.uuid4().hex[:12];names=[];stop=threading.Event();errors=[];worker=None
        evidence=os.environ.get('KARTHOS_PINNED_CAPTURE_EVIDENCE')
        destination=Path(evidence) if evidence else None
        if destination is not None:destination.mkdir(parents=True,exist_ok=False)
        def docker(*args):
            return subprocess.run(['docker',*args],capture_output=True,text=True,check=True,timeout=45).stdout.strip()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();source.chmod(0o777)
            config={'receivers':{'otlp':{'protocols':{'http':{'endpoint':'0.0.0.0:4318'}}}},
                    'exporters':{'file':{'path':'/out/traces.json','format':'json','flush_interval':'1s','append':True}},
                    'service':{'pipelines':{'traces':{'receivers':['otlp'],'exporters':['file']}}}}
            (root/'config.json').write_text(json.dumps(config))
            try:
                collector='capture-v2-writer-'+token;reader='capture-v2-reader-'+token
                docker('run','-d','--name',collector,'--label','evaluation.capture-v2='+token,
                       '--user','0:0','-p','127.0.0.1::4318','-v',str(root/'config.json')+':/etc/fixture.json:ro',
                       '-v',str(source)+':/out',COLLECTOR,'--config=/etc/fixture.json');names.append(collector)
                docker('run','-d','--name',reader,'--label','evaluation.capture-v2='+token,
                       '--cpus','0.1','--memory','64m','-v',str(source)+':/diag:ro',
                       '--entrypoint','sh',READER,'-c','sleep 120');names.append(reader)
                endpoint='http://'+docker('port',collector,'4318/tcp').splitlines()[0]+'/v1/traces'
                startup_errors=[]
                ready_deadline=time.monotonic()+10
                while True:
                    try:
                        request=urllib.request.Request(endpoint,b'{\"resourceSpans\":[]}',{'Content-Type':'application/json'})
                        with urllib.request.urlopen(request,timeout=1) as response:self.assertEqual(response.status,200)
                        break
                    except Exception as exc:
                        startup_errors.append(type(exc).__name__)
                        if time.monotonic()>=ready_deadline:raise
                        time.sleep(.05)
                def write():
                    until=time.monotonic()+25
                    while not stop.is_set() and time.monotonic()<until:
                        now=time.time_ns();trace=uuid.uuid4().hex
                        spans=[{'traceId':trace,'spanId':uuid.uuid4().hex[:16],'name':'controlled-fixture',
                                'kind':2,'startTimeUnixNano':str(now-1000000),'endTimeUnixNano':str(now),
                                'attributes':[{'key':'fixture.payload','value':{'stringValue':'x'*200}}]} for _ in range(100)]
                        payload={'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'capture-fixture'}}]},'scopeSpans':[{'scope':{'name':'fixture'},'spans':spans}]}]}
                        try:
                            request=urllib.request.Request(endpoint,json.dumps(payload).encode(),{'Content-Type':'application/json'})
                            with urllib.request.urlopen(request,timeout=2) as response:self.assertEqual(response.status,200)
                        except Exception as exc:
                            errors.append(type(exc).__name__+': '+str(exc))
                        stop.wait(.02)
                worker=threading.Thread(target=write,name='bounded-fixture-writer');worker.start()
                until=time.monotonic()+10
                while (not (source/'traces.json').exists() or not (source/'traces.json').stat().st_size) and time.monotonic()<until:time.sleep(.05)
                self.assertTrue((source/'traces.json').exists() and (source/'traces.json').stat().st_size,'collector produced no data')
                receipts=[]
                for number in range(4):
                    self.assertTrue(worker.is_alive(),'fixture producer ended unexpectedly')
                    with CollectorTailStream(['docker','exec','-i',reader,'sh','-c',reader_script('traces.json',4*1024*1024)],8*1024*1024) as stream:
                        def read(offset):
                            requested=time.time_ns();response=stream.exchange(offset,10);received=time.time_ns()
                            inode,size,data=decode_frame(response,offset)
                            return dict(inode=inode,size=size,data=data,requested_at_ns=requested,received_at_ns=received)
                        target=root/('snapshot-'+str(number)+'.json')
                        receipt=freeze_prefix(read,target)
                    rows=[json.loads(line) for line in target.read_bytes().splitlines()]
                    self.assertTrue(rows);self.assertTrue(receipt['complete']);receipts.append(receipt)
                    self.assertLessEqual(receipt['sealed_bytes']-receipt['cutoff_bytes'],16*1024*1024)
                self.assertTrue(any(r['sealed_bytes']>r['cutoff_bytes'] for r in receipts),
                                'fixture did not exercise a split writer record')
                stop.set();worker.join(timeout=5);self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
                if destination is not None:
                    import shutil
                    shutil.copy2(source/'traces.json',destination/'source-traces.json')
                    for path in root.glob('snapshot-*.json'):shutil.copy2(path,destination/path.name)
                    (destination/'receipt.json').write_text(json.dumps({'collector':COLLECTOR,'reader':READER,'receipts':receipts,'producer_errors':errors,'startup_errors':startup_errors,'fixture':'bounded synthetic OTLP; not an application pilot'},indent=2)+'\n')
            finally:
                original_error=sys.exc_info()[1];diagnostic_errors=[];cleanup_errors=[]
                try:
                    stop.set()
                    if worker is not None:
                        worker.join(timeout=5)
                        if worker.is_alive():cleanup_errors.append({'stage':'producer-stop','error':'producer thread remains alive'})
                    if destination is not None:
                        for name in names:
                            try:
                                log=subprocess.run(['docker','logs',name],capture_output=True,text=True,timeout=10)
                                (destination/(name+'.log')).write_text(log.stdout+log.stderr)
                                if log.returncode:diagnostic_errors.append({'stage':'logs','container':name,'returncode':log.returncode})
                            except Exception as exc:
                                diagnostic_errors.append({'stage':'logs','container':name,'type':type(exc).__name__,'error':str(exc)})
                            try:
                                (destination/(name+'-inspect.json')).write_text(docker('inspect',name))
                            except Exception as exc:
                                diagnostic_errors.append({'stage':'inspect','container':name,'type':type(exc).__name__,'error':str(exc)})
                except Exception as exc:
                    diagnostic_errors.append({'stage':'diagnostics','type':type(exc).__name__,'error':str(exc)})
                finally:
                    # A diagnostics failure must never bypass exact owned-container cleanup.
                    for name in reversed(names):
                        try:
                            labels=json.loads(docker('inspect',name,'--format','{{json .Config.Labels}}'))
                            if labels.get('evaluation.capture-v2')!=token:
                                raise RuntimeError('container ownership differs; cleanup refused')
                            docker('rm','-f',name)
                        except Exception as exc:
                            cleanup_errors.append({'stage':'container-cleanup','container':name,'type':type(exc).__name__,'error':str(exc)})
                if destination is not None:
                    try:
                        (destination/'producer-status.json').write_text(json.dumps({
                            'errors':errors,'containers':names,'diagnostic_errors':diagnostic_errors,
                            'cleanup_errors':cleanup_errors,
                            'test_failure':None if original_error is None else {'type':type(original_error).__name__,'error':str(original_error)},
                        },indent=2)+'\n')
                        # Seal only after snapshots, receipt, logs, inspections and final status exist.
                        (destination/'SHA256.json').write_text(json.dumps({
                            path.name:hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in sorted(destination.iterdir()) if path.is_file() and path.name!='SHA256.json'
                        },indent=2,sort_keys=True)+'\n')
                    except Exception as exc:
                        diagnostic_errors.append({'stage':'evidence-seal','type':type(exc).__name__,'error':str(exc)})
                if original_error is None and (diagnostic_errors or cleanup_errors):
                    self.fail('fixture diagnostics or cleanup incomplete: '+json.dumps({'diagnostic_errors':diagnostic_errors,'cleanup_errors':cleanup_errors}))
