"""Validate cluster configuration with the exact initialized, locked provider."""
import os,subprocess,unittest
from pathlib import Path

class TerraformProviderValidationTests(unittest.TestCase):
 @unittest.skipUnless(os.environ.get('GKE_TERRAFORM_CLUSTER_VALIDATION_ROOT'),'requires initialized exact-provider cluster root')
 def test_cluster_provider_accepts_maintenance_policy(self):
  directory=Path(os.environ['GKE_TERRAFORM_CLUSTER_VALIDATION_ROOT']).resolve()
  source=Path(__file__).resolve().parents[2]/'infrastructure/terraform/cluster'
  for file in source.glob('*.tf'):
   self.assertEqual((directory/file.name).read_bytes(),file.read_bytes(),file.name)
  result=subprocess.run(['terraform','-chdir='+str(directory),'plan','-refresh=false','-lock=false','-input=false','-no-color'],capture_output=True,text=True,timeout=120)
  self.assertEqual(result.returncode,0,result.stdout+result.stderr)
