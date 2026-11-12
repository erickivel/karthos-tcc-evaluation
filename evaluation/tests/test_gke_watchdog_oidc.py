import json,unittest
from types import SimpleNamespace
from evaluation.gke import provision as p

class WatchdogOIDCTests(unittest.TestCase):
 def test_direct_id_token_uses_existing_openid_permission_and_stdin(self):
  calls=[]
  def command(args,**kw):
   calls.append((args,kw))
   if args==['gcloud','auth','print-access-token']:return SimpleNamespace(stdout='caller-secret\n')
   self.assertTrue(args[-1].endswith('/projects/-/serviceAccounts/scheduler%40example.invalid:generateIdToken'))
   return SimpleNamespace(stdout=json.dumps({'token':'id-secret'}))
  self.assertEqual(p.watchdog_identity_token(SimpleNamespace(command=command),'scheduler@example.invalid','https://watchdog.example'),'id-secret')
  self.assertEqual(len(calls),2)
  self.assertEqual(json.loads(calls[1][0][calls[1][0].index('--data-binary')+1]),{'audience':'https://watchdog.example','includeEmail':True})
  self.assertIn('caller-secret',calls[1][1]['input_text'])
  self.assertFalse(any('caller-secret' in arg or 'id-secret' in arg or 'impersonate-service-account' in arg for args,kw in calls for arg in args))
 def test_absent_caller_or_id_token_fails_without_weakening_iam(self):
  for caller,response in [('',{'token':'ignored'}),('caller',{})]:
   calls=[]
   def command(args,**kw):
    calls.append(args);return SimpleNamespace(stdout=caller if args[0]=='gcloud' else json.dumps(response))
   with self.assertRaises(ValueError):p.watchdog_identity_token(SimpleNamespace(command=command),'scheduler@example.invalid','https://watchdog.example')
   self.assertFalse(any('iam' in args and 'add-iam-policy-binding' in args for args in calls))
