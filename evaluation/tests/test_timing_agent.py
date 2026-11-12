import unittest
from evaluation.timing_agent import correlated_failure,diagnostic_prompt
class AgentDiagnosisTests(unittest.TestCase):
 def test_chain_requires_same_trace_dependency_and_application_evidence(self):
  spans=[{'traceId':'t','name':'POST','attributes':[{'key':'http.status_code','value':{'intValue':'301'}}]},
         {'traceId':'t','name':'ChatLLM.chat','status':{'message':'Error code: 301'}},
         {'traceId':'t','name':'POST /prompt','attributes':[{'key':'http.status_code','value':{'intValue':'500'}}]}]
  self.assertTrue(correlated_failure(spans,'t')['proximate_chain_supported'])
  spans[1]['traceId']='other';self.assertFalse(correlated_failure(spans,'t')['proximate_chain_supported'])
 def test_prompt_comes_from_preserved_failure_if_available(self):
  spans=[{'name':'user_ask_agent','attributes':[{'key':'gen_ai.input.messages','value':{'stringValue':'[{"role":"user","parts":[{"type":"text","content":"test prompt"}]}]'}}]}]
  self.assertEqual(diagnostic_prompt(spans),'test prompt')
