import unittest
from types import SimpleNamespace
from evaluation.gke.provision import not_found

class GcloudNotFoundTests(unittest.TestCase):
 def test_exact_storage_absence_from_deployed_gcloud(self):
  message='ERROR: (gcloud.storage.objects.describe) gs://example-state/control%2Factive-run.json not found: 404.\n'
  for stderr in (message,message.encode()):
   self.assertTrue(not_found(SimpleNamespace(stderr=stderr)))
 def test_permissions_transport_and_unclassified_errors_fail_closed(self):
  for message in ('Permission denied: 403.','Connection reset','not found: 403.','request 404 failed without classified absence',''):
   self.assertFalse(not_found(SimpleNamespace(stderr=message)))

 def test_exact_gke_absence_from_deployed_gcloud(self):
  message="ERROR: (gcloud.container.clusters.describe) ResponseError: code=404, message=Not found: projects/example/zones/us-central1-a/clusters/absent.\n"
  for stderr in (message,message.encode()):
   self.assertTrue(not_found(SimpleNamespace(stderr=stderr)))
  for message in ('ResponseError: code=403, message=Not found: denied','ResponseError: code=500, message=Not found: upstream','ResponseError: code=404, message=unclassified'):
   self.assertFalse(not_found(SimpleNamespace(stderr=message)))
