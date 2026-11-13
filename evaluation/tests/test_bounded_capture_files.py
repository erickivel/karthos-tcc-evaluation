import unittest,json
from evaluation.tests.test_structured_capture import objects
from evaluation import structured_capture as s
class BoundedCaptureFilesTests(unittest.TestCase):
 def test_capture_does_not_rotate_or_overwrite_active_tail(self):
  for kind in ('agent','gateway'):
   cm,work=objects(kind);plan=s.instrumentation_plan(kind,cm,work,'owner',{'observer_image':'a@sha256:x','astronomy_locust_image':'b@sha256:y'})
   config=json.loads(plan['after_configmap']['data']['config.yaml'])
   for name,exporter in config['exporters'].items():
    if name.startswith('file/evaluation-'):
     self.assertNotIn('rotation',exporter)
     self.assertTrue(exporter['append'])
     self.assertEqual(exporter['flush_interval'],'1s')
   volume=next(v for v in plan['after_workload']['spec']['template']['spec']['volumes'] if v['name']=='evaluation-capture')
   self.assertEqual(volume['emptyDir']['sizeLimit'],'512Mi')
