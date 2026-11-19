import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from evaluation.gke.monitor import controller_health
from evaluation.gke.logging_preflight import request
from evaluation.gke.provision import APIS
from evaluation.tests import test_offline_population as fixtures
from evaluation.gke import population

class LaunchRepairs(unittest.TestCase):
    def test_logging_success_is_not_denial(self):
        result=controller_health({}, {'Running':True}, json.dumps({'permission':'logging.logEntries.create','verified':True,'logging_write_accepted':True}))
        self.assertFalse(result['logging_permission_denied'])
    def test_historical_denial_is_resolved_not_current(self):
        serial='Permission logging.logEntries.create denied\n'+json.dumps({'logging_write_accepted':True,'verified':True})
        result=controller_health({}, {'Running':True}, serial)
        self.assertTrue(result['logging_permission_denied'])
        self.assertEqual(result['logging_preflight_state'],'resolved-in-retained-history')
    def test_setup_includes_controller_dependencies(self):
        self.assertTrue({'cloudresourcemanager.googleapis.com','logging.googleapis.com'} <= APIS)
    def test_safe_provider_failure_details(self):
        body={'error':{'message':'secret', 'details':[{'reason':'SERVICE_DISABLED','metadata':{'token':'secret'}}]}}
        error=HTTPError('https://example',403,'secret',{},io.BytesIO(json.dumps(body).encode()))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaises(RuntimeError) as caught:request('https://example',token='secret')
        self.assertIn('403',str(caught.exception))
        self.assertIn('SERVICE_DISABLED',str(caught.exception))
        self.assertNotIn('secret',str(caught.exception))
    def test_population_mismatch_reports_partition_and_brackets(self):
        t,v=fixtures.PopulationTests().fixture()
        v['vm']['series'][0]['increase']=0
        result=population.prove('servicegraph.request.count',t,v)
        self.assertEqual(result['state'],'insufficient')
        row=result['partition_discrepancies'][0]
        self.assertEqual((row['expected'],row['observed']),(1,0))
        self.assertEqual(row['observation_bounds'],[8,22])
        self.assertEqual(row['boundary'],'storage')

    def test_transport_timeout_retains_type_without_secret(self):
        with patch('urllib.request.urlopen',side_effect=TimeoutError('secret')):
            with self.assertRaisesRegex(RuntimeError,'stage=transport.*TimeoutError') as caught:
                request('https://example',token='secret')
        self.assertNotIn('secret',str(caught.exception))
    def test_oversized_provider_payload_is_not_reported(self):
        error=HTTPError('https://example',403,'secret',{},io.BytesIO(b'x'*20000))
        with patch('urllib.request.urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'status=403') as caught:request('https://example',token='secret')
        self.assertLess(len(str(caught.exception)),200)
    def test_missing_api_blocks_before_resource_mutation(self):
        import tempfile
        from pathlib import Path
        from evaluation.tests.test_gke_provision import FakeContext
        from evaluation.gke.provision import live_preflight
        with tempfile.TemporaryDirectory() as directory:
            ctx=FakeContext(Path(directory));original=ctx.command
            def command(args,**kwargs):
                result=original(args,**kwargs)
                if args[:3]==['gcloud','services','list']:
                    rows=json.loads(result.stdout)
                    result.stdout=json.dumps([r for r in rows if r['config']['name']!='logging.googleapis.com'])
                return result
            ctx.command=command
            with self.assertRaisesRegex(ValueError,'logging.googleapis.com'):live_preflight(ctx)
            self.assertFalse(any('apply' in c for c in ctx.calls))

    def test_malformed_response_reports_decode_stage(self):
        from unittest.mock import MagicMock
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'not-json-secret'
        with patch('urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'stage=decode') as caught:request('https://example',token='secret')
        self.assertNotIn('secret',str(caught.exception))
    def test_oversized_success_body_is_bounded(self):
        from unittest.mock import MagicMock
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'x'*65537
        with patch('urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'stage=decode'):request('https://example',token='secret')
