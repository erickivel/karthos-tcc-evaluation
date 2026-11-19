import json,tempfile,time,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation import evidence_v4 as e
from evaluation.gke import evaluate as g
from evaluation.structured_capture import RepairCapture
from evaluation.tests.test_capture_finalization_lifecycle import pod

class IncompleteConsumers(unittest.TestCase):
 def test_failed_agent_never_enters_resource_or_volume_or_host_parsers(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);raw=p/'agent/raw';raw.mkdir(parents=True)
   for name in ['receiver-input.ndjson','metrics.json']: (raw/name).write_text('{"partial":"')
   agents={'multinode':False,'valid':False,'errors':['snapshot read deadline exhausted'],'agents':[{'root':str(raw.parent),'collector_uid':'u','node':'n','capture_valid':False}]}
   with patch.object(e,'agent_evidence',return_value=agents):
    result=e.resource_scores(p,[],[],[],[],'app',0,1,{})
   self.assertEqual(result['state'],'incomplete')
   self.assertIn('snapshot read deadline exhausted',result['agent_evidence']['errors'])
   self.assertEqual(g._volume_references(p,agents),([],[]))
   self.assertEqual(g._host_observations(p,agents),[])
   self.assertEqual(g._kubernetes_observations(p,agents),[])
 def test_incomplete_gateway_does_not_crash_kubernetes_or_trace_readers(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);raw=p/'structured/gateway/raw';raw.mkdir(parents=True)
   (raw/'metrics.json').write_text('{"partial":"')
   (p/'structured/status.json').write_text(json.dumps({'components':{'gateway':{'errors':['snapshot read deadline exhausted']}}}))
   self.assertEqual(g._kubernetes_observations(p,{'agents':[]}),[])
   report=e.trace_source(p,0,1)
   self.assertEqual(report['state'],'incomplete')
   self.assertFalse(report['valid'])

class MultinodeDeadline(unittest.TestCase):
 def test_eight_queued_streams_have_bounded_transfer_time_and_queue_receipts(self):
  with tempfile.TemporaryDirectory() as d:
   cap=RepairCapture(SimpleNamespace(namespace='app'),Path(d),time.time()+300,{'capture_finalization_version':2})
   pods={}
   for n in ['a','b','c']:
    v=pod();v['metadata']=dict(v['metadata'],name='agent-'+n,uid=n);v['spec']['nodeName']=n;pods['agent:'+n]=v
   pods['gateway']=pod();cap.pods=pods
   clock=[1000.];lock=threading.Lock();data=b'{"resourceMetrics":[]}\n'
   class Reader:
    def __init__(self,*a): pass
    def __enter__(self):return self
    def __exit__(self,*a):pass
    def __call__(self,offset):
     with lock:clock[0]+=10
     return {'inode':7,'size':len(data),'data':data[offset:],'requested_at_ns':100,'received_at_ns':200}
   def inventory(*a,**k):
    kind='gateway' if 'collector' in a else 'agent'
    names=['metrics.json','traces.json' if kind=='gateway' else 'receiver-input.ndjson']
    return SimpleNamespace(stdout=''.join('file\t/diag/'+n+'\n' for n in names))
   with patch('evaluation.structured_capture.time.monotonic',side_effect=lambda:clock[0]),patch('evaluation.capture_finalization.SnapshotReader',Reader),patch.object(cap,'_get',return_value={'items':[]}),patch.object(cap,'_pods',return_value={k:v for k,v in pods.items() if k.startswith('agent:')}),patch.object(cap,'_pod',return_value=pods['gateway']),patch('evaluation.structured_capture.r.kube',side_effect=inventory):
    result=cap.freeze_snapshots()
   self.assertTrue(result['complete'],result['errors'])
   self.assertEqual(result['allowance_seconds'],120)
   self.assertLessEqual(result['elapsed_seconds'],120)
   for comp in result['components'].values():
    for row in comp['files'].values():
     self.assertIn('queue_seconds',row)
     self.assertTrue(row['complete'])


class LateFrame(unittest.TestCase):
 def test_frame_returning_after_deadline_is_retained_but_not_complete(self):
  from evaluation.capture_finalization import freeze_prefix,SnapshotIncomplete
  with tempfile.TemporaryDirectory() as d:
   clock=[10.];data=b'{}\n';p=Path(d)/'raw'
   def read(offset):
    clock[0]=12.
    return {'inode':1,'size':len(data),'data':data,'requested_at_ns':1,'received_at_ns':2}
   with patch('evaluation.capture_finalization.time.monotonic',side_effect=lambda:clock[0]):
    with self.assertRaises(SnapshotIncomplete) as failed:freeze_prefix(read,p,deadline=11.)
   self.assertFalse(failed.exception.receipt['complete'])
   self.assertEqual(p.read_bytes(),data)


class MissingOutcomeSummary(unittest.TestCase):
 def test_explicit_null_generator_is_unknown_in_every_summary(self):
  from evaluation.gke.report_summary import family_summaries,save_summary
  from evaluation.gke.recoverable_run import attempt_record
  report={'traffic':{'report':{'generator_contract':{'retained_generator':None}}}}
  self.assertEqual(family_summaries(report)['application_outcomes']['state'],'insufficient')
  item=attempt_record({'id':'w-a1','logical_id':'w'},report)
  self.assertEqual(item['application_outcomes'],'unknown')
  with tempfile.TemporaryDirectory() as d:
   (Path(d)/'gke-evaluation.json').write_text(json.dumps(report))
   save_summary(Path(d),report)
