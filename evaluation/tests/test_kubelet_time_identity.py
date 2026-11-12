import copy,unittest
from evaluation.tests import test_evidence_v4 as fixtures
from evaluation import evidence_v4 as e
class ExactKubeletTimeTests(unittest.TestCase):
 def setUp(self):
  c=fixtures.ResourceEvidenceTests();c.setUp();self.point,self.input,self.independent=c.point,c.input,c.independent
 def check(self,a,b):
  self.input['body']['pods'][0]['containers'][0]['cpu']['time']=a
  self.independent['summary']['pods'][0]['containers'][0]['cpu']['time']=b
  return e.check_resource_input(self.point,[self.input],[self.independent],{})
 def test_one_nanosecond_difference_is_not_same_measurement(self):
  r=self.check('1970-01-01T00:01:30.000000001Z','1970-01-01T00:01:30.000000002Z')
  self.assertEqual(r['conversion']['state'],'pass')
  self.assertEqual(r['independent_agreement']['state'],'incomplete')
 def test_submillisecond_difference_is_not_same_measurement(self):
  self.assertEqual(self.check(90,90.0001)['independent_agreement']['state'],'incomplete')
 def test_equivalent_timezones_keep_exact_nanosecond_identity(self):
  r=self.check('1970-01-01T00:01:30.000000001Z','1970-01-01T01:01:30.000000001+01:00')
  self.assertEqual(r['independent_agreement']['state'],'pass')
  self.assertEqual(r['measurement_timestamp_ns'],90000000001)
 def test_malformed_measurement_remains_incomplete(self):
  self.assertEqual(self.check('invalid',90)['independent_agreement']['state'],'incomplete')
if __name__=='__main__':unittest.main()
