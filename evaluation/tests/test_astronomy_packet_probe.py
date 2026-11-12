import unittest
from evaluation.diagnostics import astronomy_packet_probe as probe
class PacketProbeTargetTests(unittest.TestCase):
 def setUp(self):
  self.namespace=dict(metadata=dict(labels={'evaluation.karthos.dev/owner':'karthos-evaluation'}))
  self.agent=dict(receivers={'prometheus':dict(config=dict(scrape_configs=[dict(job_name='mesh-http')]))})
 def test_owned_explicit_mesh_source_is_required(self):
  self.assertEqual(probe.verify_target(self.namespace,self.agent,{}),['mesh-http'])
 def test_unowned_namespace_is_rejected(self):
  with self.assertRaises(ValueError):probe.verify_target(dict(metadata={}),self.agent,{})
 def test_missing_mesh_jobs_is_rejected(self):
  with self.assertRaises(ValueError):probe.verify_target(self.namespace,{}, {})
 def test_active_trace_pairing_cannot_be_substituted_for_mesh(self):
  with self.assertRaises(ValueError):probe.verify_target(self.namespace,self.agent,dict(connectors={'service_graph':{}}))
