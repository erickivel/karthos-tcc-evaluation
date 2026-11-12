import json,tempfile,unittest
from pathlib import Path
from evaluation.gke.export import export_projection,verify,redact_document
class ExportProjectionTests(unittest.TestCase):
 def test_export_redacts_credentials_but_retains_semantic_dimensions(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source=root/'source';source.mkdir();original={'labels':{'grpc_status_code':'13','http_status_code':'200','source':'mesh.istio'},'env':[{'name':'DATABASE_PASSWORD','value':'fixture-secret'}]}
   (source/'pod.json').write_text(json.dumps(original));export_projection(source,root/'out')
   exported=json.loads((root/'out/pod.json').read_text());self.assertEqual(exported['labels'],original['labels']);self.assertEqual(exported['env'][0]['value'],'<redacted>');self.assertEqual(json.loads((source/'pod.json').read_text()),original);self.assertTrue(verify(root/'out')['valid'])
 def test_secret_objects_never_export_values(self):
  self.assertEqual(redact_document({'kind':'Secret','data':{'password':'encoded'}})['data'],'<redacted>')
 def test_result_files_cannot_hide_operations_state(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'source';p.mkdir();(p/'terraform.tfstate').write_text('{}')
   with self.assertRaises(ValueError):export_projection(p,Path(t)/'out')

 def test_collector_json_lines_are_not_misread_as_broken_json(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'source';p.mkdir();raw='{"metric":"cpu"}\n{"metric":"memory"}\n';(p/'metrics.json').write_text(raw)
   export_projection(p,Path(t)/'out');self.assertEqual((Path(t)/'out/metrics.json').read_text(),raw)

 def test_compressed_telemetry_is_inspected_without_rewriting_raw_bytes(self):
  import gzip
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'source';p.mkdir();raw=gzip.compress(b'{"status":"13","source":"mesh.istio"}\n');(p/'metrics.ndjson.gz').write_bytes(raw)
   export_projection(p,Path(t)/'out');self.assertEqual((Path(t)/'out/metrics.ndjson.gz').read_bytes(),raw)
   (p/'metrics.ndjson.gz').write_bytes(gzip.compress(b'{"password":"fixture-secret"}\n'))
   with self.assertRaisesRegex(ValueError,'credential'):export_projection(p,Path(t)/'unsafe')
 def test_long_encoded_receiver_body_is_bounded_in_plain_and_compressed_exports(self):
  import gzip,subprocess,sys
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source=root/'source';source.mkdir()
   raw=(json.dumps({'body_base64':'A'*262144,'source':'kubelet'})+'\n').encode()
   (source/'receiver-input.ndjson').write_bytes(raw)
   compressed=gzip.compress(raw)
   (source/'receiver-input.ndjson.gz').write_bytes(compressed)
   (source/'connection.txt').write_text('prefix 7+postgres://tester:fixture@db.example:5432')
   code='from pathlib import Path; import sys; from evaluation.gke.export import export_projection; export_projection(Path(sys.argv[1]),Path(sys.argv[2]))'
   subprocess.run([sys.executable,'-c',code,str(source),str(root/'out')],check=True,timeout=5)
   self.assertEqual((root/'out/receiver-input.ndjson').read_bytes(),raw)
   self.assertEqual((root/'out/receiver-input.ndjson.gz').read_bytes(),compressed)
   self.assertEqual((root/'out/connection.txt').read_text(),'prefix 7+postgres://tester:<redacted>@db.example:5432')
   self.assertTrue(verify(root/'out')['valid'])

 def test_pod_receiver_projection_preserves_summary_bytes_and_redacts_both_encodings(self):
  import base64,hashlib,io,tarfile
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source=root/'source';capture=source/'structured/agent/node';raw=capture/'raw';raw.mkdir(parents=True)
   pods={'items':[{'metadata':{'uid':'fixture-pod','annotations':{'io.opentelemetry.discovery.metrics/config':'receiver:\n  password: annotation-fixture-secret\n  scheme: https\n','evaluation.example/retain':'fixture'}},'spec':{'containers':[{'name':'server','resources':{'requests':{'cpu':'100m'}},'env':[{'name':'APPLICATION_SETTING','value':'only-a-test-secret'}]}]}}]}
   summary={'node':{'nodeName':'fixture-node','cpu':{'usageNanoCores':1000}}}
   def record(path,body):
    encoded=json.dumps(body).encode()
    return json.dumps({'path':path,'status':200,'body':body,'body_base64':base64.b64encode(encoded).decode()}).encode()+b'\n'
   original=record('/pods',pods)+record('/stats/summary',summary)
   inventory=source/'extended/kubernetes-inventory.json';inventory.parent.mkdir()
   inventory.write_text(json.dumps({'inventory':pods}))
   (raw/'receiver-input.ndjson').write_bytes(original)
   with tarfile.open(capture/'raw-files-attempt-1.tar','w') as archive:
    member=tarfile.TarInfo('./receiver-input.ndjson');member.size=len(original)
    archive.addfile(member,io.BytesIO(original))
   out=root/'out';export_projection(source,out)
   projected=(out/'structured/agent/node/raw/receiver-input.ndjson').read_bytes()
   first,second=projected.splitlines(keepends=True)
   self.assertEqual(second,record('/stats/summary',summary))
   row=json.loads(first);body=base64.b64decode(row['body_base64'])
   self.assertEqual(json.loads(body),row['body'])
   self.assertNotIn(b'only-a-test-secret',body)
   self.assertNotIn(b'annotation-fixture-secret',body)
   self.assertEqual(row['body']['items'][0]['metadata']['annotations'],{'evaluation.example/retain':'fixture'})
   projected_inventory=out/'extended/kubernetes-inventory.json'
   self.assertEqual(json.loads(projected_inventory.read_text())['inventory']['items'][0]['metadata']['annotations'],{'evaluation.example/retain':'fixture'})
   with tarfile.open(root/'window.tar.gz','w:gz') as archive:
    archive.add(projected_inventory,arcname='extended/kubernetes-inventory.json')
   from evaluation.gke.export import verify_archive_privacy
   self.assertFalse(verify_archive_privacy(root/'window.tar.gz')['credential_fields_detected'])
   self.assertEqual(row['body']['items'][0]['spec']['containers'][0]['resources']['requests']['cpu'],'100m')
   self.assertEqual(row['body_projection']['original_sha256'],hashlib.sha256(json.dumps(pods).encode()).hexdigest())
   with tarfile.open(out/'structured/agent/node/raw-files-attempt-1.tar') as archive:
    self.assertEqual(archive.extractfile('./receiver-input.ndjson').read(),projected)
   self.assertEqual((raw/'receiver-input.ndjson').read_bytes(),original)
   self.assertTrue(verify(out)['valid'])

 def test_receiver_export_rejects_a_mismatched_encoded_body(self):
  import base64
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);source=root/'source';source.mkdir()
   row={'path':'/stats/summary','body':{'node':{}},'body_base64':base64.b64encode(b'{"password":"fixture"}').decode()}
   (source/'receiver-input.ndjson').write_text(json.dumps(row)+'\n')
   with self.assertRaisesRegex(ValueError,'encoded receiver response'):
    export_projection(source,root/'out')

 def test_transport_archive_cannot_hide_secret_fields(self):
  import tarfile,io
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'source';p.mkdir()
   with tarfile.open(p/'raw.tar','w') as archive:
    raw=b'{"key":"authorization","value":{"stringValue":"fixture"}}\n';member=tarfile.TarInfo('raw/traces.json');member.size=len(raw);archive.addfile(member,io.BytesIO(raw))
   with self.assertRaisesRegex(ValueError,'credential'):export_projection(p,Path(t)/'out')

 def test_sealed_outer_archive_can_include_a_redacted_config(self):
  import tarfile,io
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'source';p.mkdir()
   with tarfile.open(p/'window.tar.gz','w:gz') as archive:
    raw=b'{"password":"<redacted>"}\n';member=tarfile.TarInfo('pod.json');member.size=len(raw);archive.addfile(member,io.BytesIO(raw))
   export_projection(p,Path(t)/'out');self.assertTrue(verify(Path(t)/'out')['valid'])
