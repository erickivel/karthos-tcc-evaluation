import re,unittest
from pathlib import Path

class WatchdogStartupContractTests(unittest.TestCase):
 def test_cloud_run_source_is_materialized_by_standalone_watchdog_image(self):
  root=Path(__file__).resolve().parents[2]
  docker=(root/'infrastructure/watchdog/Dockerfile').read_text()
  cloud=(root/'infrastructure/terraform/controller/main.tf').read_text()
  sources=re.findall(r'--source=([^"\s]+)',cloud)
  copied=re.findall(r'^COPY\s+\S+\s+(\S+)',docker,re.M)
  self.assertEqual(len(sources),1)
  self.assertIn(sources[0],copied,'Cloud Run must point to the handler actually packaged in the standalone watchdog image')
