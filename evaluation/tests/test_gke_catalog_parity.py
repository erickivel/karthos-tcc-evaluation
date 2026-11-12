import unittest
from evaluation.gke.parity import compare
class CatalogParityTests(unittest.TestCase):
 def sample(self,status='0',value=2,source='kubelet'):
  return {'entity_id':'node','timestamp':'1970-01-01T00:00:01Z','value':value,'labels':{'source':source,'grpc_status_code':status}}
 def document(self,status='0',value='2'):
  return {'response':{'status':'success','data':{'result':[{'metric':{'entity_id':'node','source':'kubelet','grpc_status_code':status},'values':[[1,value]]}]}}}
 def test_generic_entity_series_matches_without_checkout_filters(self):
  self.assertEqual(compare({'data':{'samples':[self.sample()]}},self.document(),'kubelet')['state'],'pass')
 def test_range_timestamps_match_on_the_victoriametrics_millisecond_grid(self):
  sample=dict(self.sample(),timestamp='2026-11-07T04:58:15.128999949Z')
  document=self.document()
  document['response']['data']['result'][0]['values']=[[1794027495.129,'2']]
  result=compare({'data':{'samples':[sample]}},document,'kubelet')
  self.assertEqual(result['state'],'pass')
  self.assertEqual(len(result['checks']),1)

 def test_adjacent_victoriametrics_milliseconds_are_distinct(self):
  sample=dict(self.sample(),timestamp='2026-11-07T04:58:15.128999949Z')
  document=self.document()
  document['response']['data']['result'][0]['values']=[[1794027495.130,'2']]
  result=compare({'data':{'samples':[sample]}},document,'kubelet')
  self.assertEqual(result['state'],'insufficient')
  self.assertEqual(len(result['checks']),2)

 def test_status_partitions_are_not_collapsed(self):
  result=compare({'data':{'samples':[self.sample('13')]}},self.document('0'),'kubelet');self.assertEqual(result['state'],'insufficient');self.assertEqual(len(result['checks']),2)
 def test_wrong_source_and_value_remain_failures(self):
  self.assertEqual(compare({'data':{'samples':[self.sample(value=3)]}},self.document(),'kubelet')['state'],'fail')
  self.assertEqual(compare({'data':{'samples':[self.sample(source='other')]}},self.document(),'kubelet')['state'],'fail')
 def test_no_evidence_and_ambiguous_samples_are_not_zero(self):
  self.assertEqual(compare({'data':{'samples':[]}},self.document(),'kubelet')['state'],'insufficient')
  self.assertEqual(compare({'data':{'samples':[self.sample(),self.sample(value=3)]}},self.document(),'kubelet')['state'],'insufficient')

 def test_metric_unit_and_explicit_edge_must_match(self):
  sample=self.sample();sample.update(metric_name='wrong',unit='wrong',edge={'source_id':'different','target_id':'other'})
  result=compare({'data':{'samples':[sample]}},self.document(),'kubelet',metric='expected',unit='bytes')
  self.assertEqual(result['state'],'fail');self.assertTrue(any('unit' in x.get('reason','') for x in result['checks']))
