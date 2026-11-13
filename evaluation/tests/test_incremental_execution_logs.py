import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke.execution_diagnostics import ExecutionDiagnostics

class IncrementalLogTests(unittest.TestCase):
 def collector(self,path,responses,limit=2048):
  self.calls=[];it=iter(responses)
  def kube(*args,**kw):
   self.calls.append(args);return SimpleNamespace(returncode=0,stdout=next(it),stderr='')
  c=ExecutionDiagnostics(SimpleNamespace(kube=kube),path,['eval'],instance_limit_bytes=limit)
  c.started_at='2026-11-12T21:00:00Z'
  item=dict(namespace='eval',pod='app',uid='uid',container='backend',container_id='cid',restart_count=0,instance='instance')
  c._json=lambda *args:{'metadata':{'uid':'uid'},'status':{'containerStatuses':[{'name':'backend','containerID':'cid','restartCount':0}]}}
  return c,item
 def test_earlier_chunk_survives_and_next_read_uses_cursor(self):
  with tempfile.TemporaryDirectory() as d:
   first='2026-11-12T21:40:00.123456789Z first\n';second='2026-11-12T21:40:01Z next\n'
   c,item=self.collector(Path(d),[first,second]);c._logs(item);c._logs(item)
   rows=[json.loads(x) for x in (Path(d)/'log-index.ndjson').read_text().splitlines()]
   self.assertNotEqual(rows[0]['path'],rows[1]['path'])
   self.assertEqual((Path(d)/rows[0]['path']).read_text(),first)
   self.assertIn('--since-time=2026-11-12T21:40:00.123456789Z',self.calls[1])
   self.assertTrue(rows[1]['overlap_possible'])
 def test_budget_does_not_overwrite_or_silently_discard(self):
  with tempfile.TemporaryDirectory() as d:
   text='2026-11-12T21:40:00Z x\n';c,item=self.collector(Path(d),[text,text,text],limit=len(text))
   c._logs(item);c._logs(item)
   rows=[json.loads(x) for x in (Path(d)/'log-index.ndjson').read_text().splitlines()]
   self.assertEqual((Path(d)/rows[0]['path']).read_text(),text)
   self.assertEqual(rows[-1]['coverage_gap'],'instance byte budget exhausted')
 def test_partial_line_does_not_advance_cursor(self):
  with tempfile.TemporaryDirectory() as d:
   c,item=self.collector(Path(d),['2026-11-12T21:40:00Z cut','2026-11-12T21:40:01Z full\n'])
   c._logs(item);c._logs(item)
   self.assertIn('--since-time='+c.started_at,self.calls[1])
 def test_same_timestamp_lines_are_preserved_as_explicit_overlap(self):
  with tempfile.TemporaryDirectory() as d:
   a='2026-11-12T21:40:00Z a\n';b=a+'2026-11-12T21:40:00Z b\n'
   c,item=self.collector(Path(d),[a,b]);c._logs(item);c._logs(item)
   rows=[json.loads(x) for x in (Path(d)/'log-index.ndjson').read_text().splitlines()]
   self.assertIn(' b\n',(Path(d)/rows[-1]['path']).read_text())

 def test_failed_read_never_advances_cursor(self):
  with tempfile.TemporaryDirectory() as d:
   c,item=self.collector(Path(d),[])
   calls=[]
   def kube(*args,**kw):
    calls.append(args)
    return SimpleNamespace(returncode=1,stdout='2026-11-12T21:40:00Z error text\n',stderr='transport failed')
   c.ctx.kube=kube;c._logs(item);c._logs(item)
   self.assertIn('--since-time='+c.started_at,calls[-1])
   rows=[json.loads(x) for x in (Path(d)/'log-index.ndjson').read_text().splitlines()]
   self.assertNotEqual(rows[0]['path'],rows[1]['path'])
   self.assertEqual(rows[0]['returncode'],1)
 def test_identity_race_never_advances_cursor(self):
  with tempfile.TemporaryDirectory() as d:
   c,item=self.collector(Path(d),['2026-11-12T21:40:00Z raced\n','2026-11-12T21:40:01Z raced\n'])
   c._json=lambda *args:{'metadata':{'uid':'different'},'status':{}}
   c._logs(item);c._logs(item)
   self.assertIn('--since-time='+c.started_at,self.calls[-1])
 def test_restart_has_separate_chunk_identity_and_budget(self):
  with tempfile.TemporaryDirectory() as d:
   line='2026-11-12T21:40:00Z event\n';c,item=self.collector(Path(d),[line,line],limit=len(line))
   c._logs(item);other=dict(item,container_id='new',restart_count=1)
   c._json=lambda *args:{'metadata':{'uid':'uid'},'status':{'containerStatuses':[{'name':'backend','containerID':'new','restartCount':1}]}}
   c._logs(other)
   rows=[json.loads(x) for x in (Path(d)/'log-index.ndjson').read_text().splitlines()]
   self.assertNotEqual(Path(rows[0]['path']).parent,Path(rows[1]['path']).parent)
