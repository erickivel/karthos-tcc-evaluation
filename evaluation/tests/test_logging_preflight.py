import unittest
from unittest.mock import patch


class LoggingPreflightTests(unittest.TestCase):
    def test_checks_actual_metadata_identity_and_permission(self):
        from evaluation.gke.logging_preflight import verify
        responses = iter(['controller@project.iam.gserviceaccount.com', {'access_token':'secret'}, {'permissions':['logging.logEntries.create']}, {}])
        calls=[]
        def request(url, **kwargs):
            calls.append((url,kwargs));return next(responses)
        result=verify('project',request=request)
        self.assertEqual(result['principal'],'controller@project.iam.gserviceaccount.com')
        self.assertNotIn('secret',str(result))
        self.assertEqual(calls[-1][1]['token'],'secret')
        self.assertEqual(calls[-1][0],'https://logging.googleapis.com/v2/entries:write')
        self.assertTrue(result['logging_write_accepted'])
        self.assertNotIn('secret',str(calls[-1][1]['payload']))

    def test_missing_permission_blocks(self):
        from evaluation.gke.logging_preflight import verify
        responses=iter(['controller@p.iam.gserviceaccount.com',{'access_token':'secret'},{}])
        with self.assertRaisesRegex(ValueError,'logging.logEntries.create'):
            verify('p',request=lambda *a,**kw:next(responses))
