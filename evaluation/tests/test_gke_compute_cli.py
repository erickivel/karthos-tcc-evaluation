import unittest
from types import SimpleNamespace
from evaluation.gke import provision as p
from evaluation.gke.teardown import CleanupContext

class ComputeCLIContractTests(unittest.TestCase):
 def test_cli_paths_are_distinct_from_rest_collection_names(self):
  for kind,name,path in [('firewall','projects/research-project/global/firewalls/owned',['firewall-rules']),('subnetwork','projects/research-project/regions/us-central1/subnetworks/owned',['networks','subnets'])]:
   calls=[]
   def command(args,**kw):calls.append(args);return SimpleNamespace(returncode=1,stdout='',stderr="The resource was not found")
   ctx=SimpleNamespace(config={'gcp':{'project_id':'research-project'}},command=command)
   self.assertIsNone(p.cloud_get(ctx,{'kind':kind,'name':name}))
   self.assertEqual(calls[0][:2+len(path)+2],['gcloud','compute',*path,'describe','owned'])
 def test_saved_inventory_cleanup_accepts_actual_cli_paths(self):
  c=object.__new__(CleanupContext);calls=[]
  c.executor=lambda args,**kw:(calls.append(args) or SimpleNamespace(returncode=0,stdout='{}',stderr=''))
  for path in (['firewall-rules'],['networks','subnets']):
   c.command(['gcloud','compute',*path,'describe','owned'],cleanup=True)
  self.assertEqual(len(calls),2)
 def test_cleanup_refuses_creates_and_obsolete_rest_aliases(self):
  c=object.__new__(CleanupContext);c.executor=lambda *a,**kw:self.fail('unsupported command reached executor')
  for path,operation in [(['firewall-rules'],'create'),(['networks','subnets'],'create'),(['firewalls'],'describe'),(['subnetworks'],'describe')]:
   with self.assertRaises(ValueError):c.command(['gcloud','compute',*path,operation,'owned'],cleanup=True)
