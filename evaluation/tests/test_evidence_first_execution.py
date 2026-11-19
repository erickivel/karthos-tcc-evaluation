import contextlib,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke import recoverable_run as r
from evaluation.gke.models import write

class EvidenceFirstExecutionTests(unittest.TestCase):
 def run_case(self,analysis_failure=False,raw_valid=True,analysis_export_failure=False,complement=False,invalid=False,profile=None,numerical=False,history_outcomes=None):
  with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as stack:
   p=Path(d);events=[]
   spec={'id':'w','repeat':1,'cell':'astronomy-mesh','demo':'astronomy','phase':'smoke','duration_seconds':60,'scenario':'normal'}
   ctx=SimpleNamespace(run_id="test",result=p,config={'evidence_pipeline':'sealed-before-analysis-v1','limits':{}},manifest={'matrix':[spec],'schedule':'thesis-repair-validation-v2'},state={'resources':[]},write=write,event=lambda *a,**kw:None)
   if complement or profile:
    ctx.config['execution_profile']=profile or 'boutique-complement-v1'
    ctx.manifest['matrix']=[spec,dict(spec,id='second')]
   if history_outcomes is not None:
    spec.update(cell='boutique-mesh',demo='boutique',phase='baseline',history_observation=True)
    ctx.manifest.update(schedule='thesis-completion-v2',matrix=[spec,dict(spec,id='second',history_observation=False)])
    history_results=iter(history_outcomes)
   class Driver:
    nodes=[];paths={}
    def start_repeat(self,n):events.append('cluster')
    def setup(self,s):events.append('setup')
    def window(self,s):
     events.append('measure');q=p/s['id'];q.mkdir();self.paths[s['id']]=q
     return {'analysis':{'state':'pending'},'execution':{'state':'complete'},'evidence':{'valid':False}}
    def upload(self,s,report):events.append('raw-export');return {'verified':raw_valid}
    def analyse(self,s,receipt):
     events.append('analyse');return {'analysis':{'state':'failed' if analysis_failure else 'complete'},'execution':{'state':'complete'},'evidence':{'valid':not analysis_failure and not invalid},'path':str(self.paths[s['id']]),'thesis_answers':{'numerical':{'state':'fail' if numerical else 'pass'}}}
    def upload_analysis(self,s,report):
     events.append('analysis-export')
     if analysis_export_failure:raise RuntimeError('analysis export failed')
     return {'verified':True}
    def thesis_history(self,s):
     events.append('history')
     outcome=next(history_results)
     if isinstance(outcome,Exception):raise outcome
     if isinstance(outcome,dict):return outcome
     return {'answer':{'state':outcome},'export':{'verified':True},'continuation_safe':True}
    def finish(self):events.append('finish');return {'verified':True}
   for name in ('_budget','_checkpoint'):
    stack.enter_context(patch.object(r,name,return_value={'verified':True}))
   stack.enter_context(patch.object(r,'scheduled_matrix',return_value=ctx.manifest['matrix']))
   def barrier(*a):events.append('barrier');return {'safe':True}
   stack.enter_context(patch.object(r,'transition',side_effect=barrier))
   stack.enter_context(patch('evaluation.gke.provision.require_context'))
   stack.enter_context(patch('evaluation.gke.provision.assert_project_lease'))
   stack.enter_context(patch('evaluation.gke.minimal_readiness.check_cell',return_value={'usable':True,'cell':'astronomy-mesh'}))
   diag=stack.enter_context(patch('evaluation.gke.execution_diagnostics.ExecutionDiagnostics'))
   diag.return_value.stop.return_value={'stopped':True}
   diag.side_effect=lambda ctx,path,**kwargs: (path.mkdir(parents=True,exist_ok=True) or SimpleNamespace(start=lambda:None,stop=lambda:{'stopped':True}))
   stack.enter_context(patch('evaluation.gke.export.render_matrix'))
   stack.enter_context(patch('evaluation.gke.thesis.claim_matrix',return_value={'claims':[]}))
   stack.enter_context(patch('evaluation.gke.thesis.required_claims_confirmed',return_value=False))
   result=r.execute(ctx,Driver())
   return events,result
 def test_barrier_and_verified_raw_export_precede_analysis(self):
  events,result=self.run_case()
  self.assertEqual(events,['cluster','setup','measure','barrier','raw-export','analyse','analysis-export','finish'])
  self.assertEqual(result['attempts'],1)
 def test_failed_analysis_never_repeats_traffic(self):
  events,result=self.run_case(analysis_failure=True)
  self.assertEqual(events.count('measure'),1)
  self.assertEqual(result['records'][0]['attempts'][0]['analysis']['state'],'failed')
 def test_failed_raw_export_blocks_analysis(self):
  events,result=self.run_case(raw_valid=False)
  self.assertNotIn('analyse',events)
  self.assertEqual(result['state'],'incomplete')

 def test_analysis_export_failure_preserves_measurement_for_continuation(self):
  events,result=self.run_case(analysis_export_failure=True)
  attempt=result['records'][0]['attempts'][0]
  self.assertTrue(attempt['export']['verified'])
  self.assertTrue(attempt['retry_policy_complete'])
  self.assertEqual(events.count('measure'),1)

 def test_complement_stops_after_one_operational_retry_and_cleans(self):
  events,result=self.run_case(complement=True,invalid=True)
  self.assertEqual(events.count('measure'),2)
  self.assertEqual(result['records'][-1]['state'],'unattempted')
  self.assertEqual(events[-1],'finish')

 def test_full_stops_after_one_operational_retry(self):
  events,result=self.run_case(profile='thesis-robustness-full-v1',invalid=True)
  self.assertEqual(events.count('measure'),2)
  self.assertEqual(result['records'][-1]['state'],'unattempted')
  self.assertEqual(events[-1],'finish')

 def test_full_numerical_disagreement_does_not_retry(self):
  events,result=self.run_case(profile='thesis-robustness-full-v1',numerical=True)
  self.assertEqual(events.count('measure'),2)
  self.assertTrue(all(len(row['attempts'])==1 for row in result['records']))

 def test_full_history_exhaustion_stops_remaining_windows(self):
  for outcomes in ([RuntimeError('capture'),RuntimeError('capture')],['insufficient','insufficient']):
   with self.subTest(outcomes=outcomes):
    events,result=self.run_case(profile='thesis-robustness-full-v1',history_outcomes=outcomes)
    self.assertEqual(events.count('history'),2)
    self.assertEqual(events.count('measure'),1)
    self.assertEqual(result['records'][-1]['state'],'unattempted')
    self.assertEqual(events[-1],'finish')

 def test_full_history_numerical_failure_continues_without_retry(self):
  events,result=self.run_case(profile='thesis-robustness-full-v1',history_outcomes=['incorrect'])
  self.assertEqual(events.count('history'),1)
  self.assertEqual(events.count('measure'),2)
  self.assertEqual(result['history']['answer']['state'],'incorrect')
  self.assertEqual(len(result['history']['attempts']),1)
  self.assertEqual(result['history']['attempts'][0]['report']['answer']['state'],'incorrect')

 def test_full_history_safe_operational_retry_can_recover(self):
  events,result=self.run_case(profile='thesis-robustness-full-v1',history_outcomes=['insufficient','correct'])
  self.assertEqual(events.count('history'),2)
  self.assertEqual(events.count('measure'),2)
  self.assertEqual(result['history']['answer']['state'],'correct')

 def test_historical_profile_history_failure_keeps_original_behavior(self):
  events,result=self.run_case(history_outcomes=['incorrect'])
  self.assertEqual(events.count('measure'),2)
  self.assertEqual(events.count('history'),1)
