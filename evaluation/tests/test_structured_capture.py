import copy,io,json,tarfile,tempfile,unittest
from unittest.mock import patch
import importlib.util
from pathlib import Path
from evaluation import structured_capture as s

def objects(kind='agent'):
 config={'receivers':{'kubelet_stats':{'endpoint':'https://${env:NODE_IP}:10250','insecure_skip_verify':True}},'exporters':{'otlp':{}},'service':{'pipelines':{'metrics':{'receivers':['kubelet_stats'],'exporters':['otlp']},'traces':{'receivers':['otlp'],'processors':['batch'],'exporters':['otlp']}}}}
 return {'metadata':{'name':'karthos-'+kind+'-config','uid':'cm-uid'},'data':{'config.yaml':json.dumps(config)}},{'metadata':{'name':'karthos-'+kind,'uid':'work-uid'},'spec':{'template':{'metadata':{'labels':{'app':'collector'}},'spec':{'containers':[{'name':kind,'image':'collector@sha256:abc'}]}}}}
class TransformTests(unittest.TestCase):
 def plan(self,kind='agent'):
  cm,work=objects(kind);return s.instrumentation_plan(kind,cm,work,'owner-1',{'observer_image':'reader@sha256:a','astronomy_locust_image':'python@sha256:b'})
 @unittest.skipUnless(importlib.util.find_spec('yaml'),'requires pinned runtime PyYAML')
 def test_real_yaml_capture_uses_python_parser_without_ruby(self):
  import yaml
  for kind in ('agent','gateway'):
   with self.subTest(kind=kind):
    cm,work=objects(kind);expected=json.loads(cm['data']['config.yaml']);cm['data']['config.yaml']=yaml.safe_dump(expected)
    with patch.object(s.subprocess,'run',side_effect=AssertionError('controller must not require Ruby for YAML')):
     plan=s.instrumentation_plan(kind,cm,work,'owner-1',{'observer_image':'reader@sha256:a','astronomy_locust_image':'python@sha256:b'})
    emitted=json.loads(plan['after_configmap']['data']['config.yaml'])
    self.assertEqual(emitted['service']['pipelines']['metrics']['receivers'],expected['service']['pipelines']['metrics']['receivers'])
    self.assertIn('file/evaluation-metrics',emitted['exporters'])

 def test_agent_preserves_input_and_proxies_exact_receiver_id(self):
  cm,work=objects();before=copy.deepcopy((cm,work));plan=s.instrumentation_plan('agent',cm,work,'owner-1',{'observer_image':'reader@sha256:a','astronomy_locust_image':'python@sha256:b'});config=json.loads(plan['after_configmap']['data']['config.yaml'])
  self.assertEqual(config['receivers']['kubelet_stats']['endpoint'],'http://127.0.0.1:19050');self.assertEqual((cm,work),before);self.assertEqual(config['exporters']['file/evaluation-metrics']['format'],'json');self.assertNotIn('compression',config['exporters']['file/evaluation-metrics'])
  self.assertIn('diag-kubelet-proxy',[c['name'] for c in plan['after_workload']['spec']['template']['spec']['containers']])
 def test_gateway_raw_ingress_is_before_batch(self):
  config=json.loads(self.plan('gateway')['after_configmap']['data']['config.yaml']);self.assertEqual(config['service']['pipelines']['traces/evaluation-ingress']['processors'],[]);self.assertEqual(config['service']['pipelines']['traces']['processors'],['batch']);self.assertEqual(config['service']['pipelines']['metrics']['exporters'][0],'otlp')
 def test_restore_preserves_user_changes_and_tests_uid_and_original_value(self):
  plan=self.plan();current=copy.deepcopy(plan['after_workload']);current['spec']['template']['spec']['containers'][0]['image']='user-change'
  with self.assertRaisesRegex(ValueError,'changed'):s.restore_patch(plan,'workload',current)
  current=copy.deepcopy(plan['after_configmap']);current['metadata']['uid']='replacement'
  with self.assertRaisesRegex(ValueError,'UID'):s.restore_patch(plan,'configmap',current)
  patch=s.restore_patch(plan,'workload',plan['after_workload']);self.assertEqual(patch[0],{'op':'test','path':'/metadata/uid','value':'work-uid'});self.assertTrue(any(x['op']=='test' and x['path']=='/spec/template' for x in patch))
class ArchiveTests(unittest.TestCase):
 def archive(self,path,entries):
  with tarfile.open(path,'w') as tar:
   for name,data in entries:
    info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
 def test_preserve_rotated_json_and_refuse_overwrite(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.archive(p/'a.tar',[('metrics.json',b'{"resourceMetrics":[]}\n'),('metrics-2026.json',b'{"resourceMetrics":[]}\n')]);state=s.extract_archive(p/'a.tar',p/'raw',1024);self.assertEqual(len(state['files']),2);self.assertEqual(state['errors'],['rotation:metrics-2026.json'])
   with self.assertRaises(FileExistsError):s.extract_archive(p/'a.tar',p/'raw',1024)
 def test_tar_traversal_and_total_size_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);self.archive(p/'a.tar',[('../escape',b'x')])
   with self.assertRaises(ValueError):s.extract_archive(p/'a.tar',p/'raw',1024)
   self.archive(p/'b.tar',[('metrics.json',b'x'*20)])
   with self.assertRaises(ValueError):s.extract_archive(p/'b.tar',p/'raw2',10)

class SourceContractTests(unittest.TestCase):
 def test_rejects_preexisting_diagnostic_mount_and_non_serviceaccount_auth(self):
  cm,work=objects();work['spec']['template']['spec']['volumes']=[{'name':'evaluation-capture','hostPath':{'path':'/user'}}]
  with self.assertRaisesRegex(ValueError,'volume'):s.instrumentation_plan('agent',cm,work,'x',{'observer_image':'r@sha256:a','astronomy_locust_image':'p@sha256:b'})
 def test_empty_otlp_batches_are_not_source_evidence(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'metrics.json').write_text('{"resourceMetrics":[]}\n')
   self.assertEqual(s.signal_coverage(p,'Metrics',(100,200),30)['records'],0)
   (p/'metrics.json').write_text('{"resourceMetrics":[{"scopeMetrics":[{"metrics":[{"name":"cpu","gauge":{"dataPoints":[{"asInt":"1"}]}}]}]}]}\n')
   self.assertEqual(s.signal_coverage(p,'Metrics',(100,200),30)['records'],1)
