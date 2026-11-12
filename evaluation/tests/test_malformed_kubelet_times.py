import unittest
from evaluation.tests import test_evidence_v4 as fixtures
from evaluation import evidence_v4 as e
class MalformedKubeletTimeTests(unittest.TestCase):
 def test_malformed_receiver_time_stays_incomplete(self):
  for value in (True,{},[],float('nan'),float('inf')):
   with self.subTest(value=value):
    f=fixtures.ResourceEvidenceTests();f.setUp()
    f.input['body']['pods'][0]['containers'][0]['cpu']['time']=value
    result=e.check_resource_input(f.point,[f.input],[f.independent],{})
    self.assertEqual(result['conversion']['state'],'pass')
    self.assertEqual(result['independent_agreement']['state'],'incomplete')
 def test_malformed_independent_time_stays_incomplete(self):
  for value in (True,{},[],float('nan'),float('inf')):
   with self.subTest(value=value):
    f=fixtures.ResourceEvidenceTests();f.setUp()
    f.independent['summary']['pods'][0]['containers'][0]['cpu']['time']=value
    self.assertEqual(e.check_resource_input(f.point,[f.input],[f.independent],{})['independent_agreement']['state'],'incomplete')
if __name__=='__main__':unittest.main()
