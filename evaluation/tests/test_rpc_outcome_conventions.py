import unittest
from evaluation import evidence_v4 as e
class RpcOutcomeConventionTests(unittest.TestCase):
 def source(self,attributes):
  c=dict(trace_id='t',span_id='c',parent_id='outside',service='frontend',kind='client',start=100,end=100.05,failed=False,attributes={})
  s=dict(trace_id='t',span_id='s',parent_id='c',service='checkout',kind='server',name='oteldemo.CheckoutService/PlaceOrder',start=100.01,end=100.04,failed=False,attributes=attributes)
  return e.trace_accounting([c,s],99,101)
 def test_modern_grpc_ok_and_internal_are_observed_outcomes(self):
  for status,code in [('OK',0),('INTERNAL',13)]:
   with self.subTest(status=status):
    r=self.source({'rpc.system.name':'grpc','rpc.response.status_code':status})
    self.assertEqual(r['grpc_outcome_unknown'],0)
    self.assertEqual(r['grpc_outcome_errors'],int(code!=0))
    self.assertEqual(r['spans'][0]['grpc_status_code'],code)
    self.assertEqual(r['errors'],0,'connector native span status remains separate')
 def test_legacy_numeric_and_modern_equivalent_preserve_both(self):
  r=self.source({'rpc.grpc.status_code':13,'rpc.system.name':'grpc','rpc.response.status_code':'INTERNAL'})
  self.assertEqual(r['spans'][0]['grpc_status_code'],13)
  self.assertEqual(r['spans'][0]['grpc_status_attributes']['rpc.grpc.status_code'],13)
  self.assertEqual(r['spans'][0]['grpc_status_attributes']['rpc.response.status_code'],'INTERNAL')
 def test_conflicting_conventions_are_unknown(self):
  r=self.source({'rpc.grpc.status_code':0,'rpc.system.name':'grpc','rpc.response.status_code':'INTERNAL'})
  self.assertEqual(r['grpc_outcome_unknown'],1)
  self.assertEqual(r['grpc_outcome_errors'],0)
  self.assertEqual(r['spans'][0]['grpc_status_state'],'conflict')
 def test_other_rpc_system_and_malformed_values_remain_unknown(self):
  for attrs in ({'rpc.system.name':'jsonrpc','rpc.response.status_code':'INTERNAL'}, {'rpc.system.name':'grpc','rpc.response.status_code':True},{'rpc.grpc.status_code':True},{'rpc.grpc.status_code':99},{}):
   with self.subTest(attrs=attrs):self.assertEqual(self.source(attrs)['grpc_outcome_unknown'],1)
if __name__=='__main__':unittest.main()
