import hashlib,json,tempfile,unittest
from pathlib import Path
from evaluation import repair_pilot as p
class RecurrenceReadinessTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
  self.cause=dict(state='recurrence_validated',historical_cause='unknown',readiness_policy='user-approved-recurrence-v1',decision=self.record('decision.json',dict(approval='recurrence_based_readiness',user_reply='Use clean reruns for readiness; retain the original cause as unknown')),attempts=[])
  from evaluation.tests.test_generator_artifacts import bundle,rehash
  from evaluation.workloads.common import journey
  for n in range(3):
   run=f'control-{n}';window=self.root/f'window-{n}';window.mkdir()
   manifest=dict(demo='astronomy',source='mesh',generator='k6',workload='checkout-heavy',duration_seconds=60,rate=2,protocol_version=4,run_id=run,generator_artifacts_complete=True,start="2026-10-20T00:00:00Z",end="2026-10-20T00:01:00Z")
   ledger=[]
   for iteration in range(120):
    for index,step in enumerate(journey('astronomy','checkout-heavy',iteration,run)):
     ledger.append(dict(run_id=run,request_id=f'{run}:{iteration}:{index}',iteration=iteration,step_index=index,route=step['route'],status=step['expected_status'],expected_status=step['expected_status'],outcome_ok=True,completed=True,exception=None,started_at="2026-10-20T00:00:01Z",completed_at="2026-10-20T00:00:01.001Z"))
   score=dict(manifest=manifest,generator=dict(requests=len(ledger),completed_requests=len(ledger)),oracle_valid=True,source_valid=True,evidence=dict(valid=True,capture_valid=True),execution=dict(state='complete'),application_outcomes=dict(state='pass',unexpected=0))
   artifacts=window/'generator-artifacts';bundle(artifacts)
   first=dict(run_id=run,started_at='2026-10-20T00:00:00Z',ended_at=None,intended_duration_seconds=60,target_iterations_per_second=2,expected_iterations=120)
   events=[('WINDOW',first)]+[event for row in ledger for event in [('ATTEMPT',dict(run_id=run,request_id=row['request_id'])),('REQUEST',row)]]+[('WINDOW',dict(first,ended_at='2026-10-20T00:01:00Z'))]
   (artifacts/'generator.log').write_text(''.join('EVAL_'+kind+' '+json.dumps(row)+'\n' for kind,row in events))
   (artifacts/'run-id.txt').write_text(run)
   (artifacts/'workload-ended-at.txt').write_text('2026-10-20T00:01:00Z')
   (artifacts/'k6-summary.json').write_text(json.dumps(dict(metrics=dict(http_reqs=dict(values=dict(count=len(ledger))),dropped_iterations=dict(values=dict(count=0))))))
   (artifacts/'k6.ndjson').write_text(json.dumps(dict(type='Point',metric='http_reqs',data=dict(value=len(ledger))))+'\n');rehash(artifacts)
   attempt={kind:self.record(f'window-{n}/{kind}.json',data,ndjson=kind=='ledger') for kind,data in [('manifest',manifest),('verification',score),('ledger',ledger)]}
   attempt['artifact_manifest']=dict(path=str((artifacts/'SHA256SUMS').relative_to(self.root)),sha256=hashlib.sha256((artifacts/'SHA256SUMS').read_bytes()).hexdigest())
   self.cause['attempts'].append(attempt)

 def record(self,name,data,ndjson=False):
  path=self.root/name;path.write_text(''.join(json.dumps(row)+'\n' for row in data) if ndjson else json.dumps(data)+'\n')
  return dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
 def rewrite(self,entry,change):
  path=self.root/entry['path'];lines=path.read_text().splitlines();data=json.loads(lines[0]);data.update(change);lines[0]=json.dumps(data);path.write_text('\n'.join(lines)+'\n');entry['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 def issues(self):return p.recurrence_readiness_issues(self.cause,self.root)
 def test_three_valid_clean_attempts_can_pass_with_historical_unknown(self):self.assertEqual(self.issues(),[])
 def test_503_even_with_forged_pass_score_is_rejected(self):
  self.rewrite(self.cause['attempts'][0]['ledger'],dict(status=503));self.assertTrue(self.issues())
 def test_invalid_capture_cannot_pass(self):
  self.rewrite(self.cause['attempts'][0]['verification'],dict(evidence=dict(valid=False,capture_valid=False)));self.assertTrue(self.issues())
 def test_missing_hashed_decision_and_changed_evidence_are_rejected(self):
  self.cause['decision']['sha256']='changed';self.assertTrue(self.issues())
 def test_fewer_attempts_or_reused_identity_are_rejected(self):
  self.cause['attempts'].pop();self.assertTrue(self.issues())
 def test_wrong_source_or_unexpected_business_result_is_rejected(self):
  self.rewrite(self.cause['attempts'][0]['manifest'],dict(source='trace'));self.assertTrue(self.issues())
 def test_unknown_cause_is_not_relabelled_resolved(self):
  self.cause['historical_cause']='resolved';self.assertTrue(self.issues())
 def test_substituted_verification_cannot_certify_another_attempt(self):
  self.cause['attempts'][1]['verification']=self.cause['attempts'][0]['verification']
  self.assertTrue(self.issues())
 def test_wrong_embedded_manifest_or_scored_population_is_rejected(self):
  self.rewrite(self.cause['attempts'][0]['verification'],dict(manifest=dict(run_id='other',source='trace'),generator=dict(requests=1000)))
  self.assertTrue(self.issues())
 def test_sanitized_ledger_cannot_omit_raw_generator_record(self):
  entry=self.cause['attempts'][0]['ledger'];path=self.root/entry['path'];path.write_text('\n'.join(path.read_text().splitlines()[1:])+'\n');entry['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
  self.assertTrue(self.issues())
 def test_corrupt_raw_artifact_is_rejected(self):
  path=self.root/self.cause['attempts'][0]['artifact_manifest']['path'];(path.parent/'generator.log').write_text('corrupt')
  self.assertTrue(self.issues())
 def replace_window(self,index,ledger=None,manifest=None,score=None):
  from evaluation.tests.test_generator_artifacts import rehash
  item=self.cause['attempts'][index];window=self.root/item['manifest']['path'];window=window.parent
  m=json.loads((window/'manifest.json').read_text());s=json.loads((window/'verification.json').read_text())
  records=[json.loads(x) for x in (window/'ledger.json').read_text().splitlines()]
  if ledger is not None:records=ledger
  if manifest is not None:m=manifest
  if score is not None:s=score
  s['manifest']=m
  for kind,data in [('manifest',m),('verification',s),('ledger',records)]:item[kind]=self.record(f'window-{index}/{kind}.json',data,ndjson=kind=='ledger')
  artifacts=window/'generator-artifacts';events=(artifacts/'generator.log').read_text().splitlines();markers=[json.loads(x.split(' ',1)[1]) for x in events if x.startswith('EVAL_WINDOW ')]
  markers[-1]['ended_at']='2026-10-20T00:01:01Z'
  (artifacts/'generator.log').write_text('EVAL_WINDOW '+json.dumps(markers[0])+'\n'+''.join('EVAL_'+kind+' '+json.dumps(row)+'\n' for row in records for kind in ('ATTEMPT','REQUEST'))+'EVAL_WINDOW '+json.dumps(markers[-1])+'\n')
  (artifacts/'workload-ended-at.txt').write_text('2026-10-20T00:01:01Z')
  (artifacts/'k6-summary.json').write_text(json.dumps(dict(metrics=dict(http_reqs=dict(values=dict(count=len(records))),dropped_iterations=dict(values=dict(count=0))))))
  (artifacts/'k6.ndjson').write_text(json.dumps(dict(type='Point',metric='http_reqs',data=dict(value=len(records))))+'\n');rehash(artifacts)
  item['artifact_manifest']['sha256']=hashlib.sha256((artifacts/'SHA256SUMS').read_bytes()).hexdigest()

 def late_records(self,index=0):
  from evaluation.workloads.common import journey
  records=[json.loads(x) for x in (self.root/f'window-{index}/ledger.json').read_text().splitlines()];run=records[0]['run_id']
  for step_index,step in enumerate(journey('astronomy','checkout-heavy',120,run)):
   records.append(dict(run_id=run,request_id=f'{run}:120:{step_index}',iteration=120,step_index=step_index,route=step['route'],status=step['expected_status'],expected_status=step['expected_status'],outcome_ok=True,completed=True,exception=None,started_at='2026-10-20T00:01:00.001Z',completed_at='2026-10-20T00:01:00.006Z'))
  return records

 def test_clean_late_records_remain_raw_but_do_not_inflate_scored_window(self):
  self.replace_window(0,ledger=self.late_records());self.assertEqual(self.issues(),[])

 def test_late_503_unexpected_or_incomplete_records_still_block(self):
  baseline=self.late_records()
  for change in ({'status':503},{'outcome_ok':False},{'completed':False},{'exception':'timeout'}):
   records=[dict(row) for row in baseline];records[-1].update(change);self.replace_window(0,ledger=records);self.assertTrue(self.issues())

 def test_exact_end_excluded_but_inflight_completion_included(self):
  records=self.late_records();records=records[:-len([x for x in records if x['iteration']==120])]
  records[0].update(started_at='2026-10-20T00:00:59.999Z',completed_at='2026-10-20T00:01:00.005Z')
  records[1].update(started_at='2026-10-20T00:01:00Z',completed_at='2026-10-20T00:01:00.006Z')
  score=json.loads((self.root/'window-0/verification.json').read_text());score['generator']={'requests':len(records)-1,'completed_requests':len(records)-1}
  self.replace_window(0,ledger=records,score=score);self.assertEqual(self.issues(),[])

 def test_invalid_missing_nonfinite_or_shifted_bounds_rejected(self):
  original=json.loads((self.root/'window-0/manifest.json').read_text())
  for patch in ({'start':None},{'end':float('nan')},{'start':True},{'end':'2026-10-19T23:59:59Z'},{'start':'2026-10-20T00:00:00.001Z','end':'2026-10-20T00:01:00.001Z'}):
   self.replace_window(0,manifest=dict(original,**patch));self.assertTrue(self.issues())

 def test_missing_or_nonfinite_request_timestamp_rejected(self):
  records=[json.loads(x) for x in (self.root/'window-0/ledger.json').read_text().splitlines()]
  for value in (None,'not-a-time',float('inf'),True,10**1000):
   records[0]['started_at']=value;self.replace_window(0,ledger=records);self.assertTrue(self.issues())

 def test_planned_journeys_cannot_all_be_moved_outside_measurement(self):
  records=[json.loads(x) for x in (self.root/'window-0/ledger.json').read_text().splitlines()]
  for row in records:row['started_at']='2026-10-20T00:01:00.001Z'
  score=json.loads((self.root/'window-0/verification.json').read_text());score['generator']={'requests':0,'completed_requests':0}
  self.replace_window(0,ledger=records,score=score);self.assertTrue(self.issues())

if __name__=='__main__':unittest.main()
