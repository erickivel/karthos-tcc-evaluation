import unittest
from evaluation.gke.claim_recovery import recover_calls
class RecoveryTests(unittest.TestCase):
 def test_reconstructed_query_is_labeled_and_input_unchanged(self):
  d={'scope':{'metric_source':'traces.servicegraph','calls_source':'traces.otlp','namespace':'app','target_id':'checkout','from':'2026-01-01T00:00:00Z','to':'2026-01-01T00:01:00Z'},'answers':{},'after':{'nodes':[{'id':'front','kind':'service','namespace':'app','name':'frontend'}],'edges':[{'source':'front','target':'checkout','relation':'calls','provenance':['traces.servicegraph'],'first_seen':'2026-01-01T00:00:01Z','last_seen':'2026-01-01T00:00:02Z'}]}}
  r,changed=recover_calls(d)
  self.assertTrue(changed);self.assertEqual(d['scope']['calls_source'],'traces.otlp')
  self.assertEqual(r['answers']['interval-callers']['value'][0]['caller'],'frontend')
  self.assertEqual(r['edge_history']['requests'],[])
  self.assertIn('not executed',r['edge_history']['recovery'])
