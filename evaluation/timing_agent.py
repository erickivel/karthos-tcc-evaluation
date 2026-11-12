"""Separate bounded agent reproduction; never a checkout accuracy gate."""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path
from . import runner as r
from .protocol import Cell


def values(span):
 return {a['key']:next(iter(a.get('value',{}).values()),None) for a in span.get('attributes',[])}

def correlated_failure(spans,trace_id):
 selected=[s for s in spans if s.get('traceId')==trace_id]
 dependency=any(str(values(s).get('http.status_code',values(s).get('http.response.status_code')))=='301' and s.get('name')=='POST' for s in selected)
 exception=any('301' in s.get('status',{}).get('message','') and s.get('name')=='ChatLLM.chat' for s in selected)
 prompt=any(str(values(s).get('http.status_code',values(s).get('http.response.status_code')))=='500' and s.get('name')=='POST /prompt' for s in selected)
 return dict(proximate_chain_supported=dependency and exception and prompt,dependency_301=dependency,
  model_exception_301=exception,prompt_500=prompt,trace_id=trace_id,spans=len(selected),
  original_dependency_selection_cause='unresolved; non-recurrence does not establish a fix')

def diagnostic_prompt(spans):
 for span in spans:
  if span.get('name')=='user_ask_agent':
   encoded=values(span).get('gen_ai.input.messages')
   if encoded:
    for message in json.loads(encoded):
     for part in message.get('parts',[]):
      if part.get('type')=='text' and part.get('content'):return part['content']
 raise ValueError('original prompt unavailable; no guessed live reproduction')

def diagnose(path,budget_seconds):
 from .structured_capture import RepairCapture
 from .timing_capture import AvailabilityCapture
 path=Path(path);path.mkdir(parents=True,exist_ok=False)
 saved=r._DEADLINE;r._DEADLINE=min(saved,time.monotonic()+budget_seconds)
 capture=availability=None
 report=dict(state='diagnostic',budget_seconds=budget_seconds,started_at=r.utc(),checkout_acceptance_gate=False,
   attempted_requests=0,errors=[])
 try:
  finding=json.loads((r.ROOT/'analyses/protocol4-final-findings-20261023/analysis.json').read_text())['native_failure']
  historical=finding['correlated_spans'];report['historical_chain']=correlated_failure(historical,finding['ledger']['trace_id'])
  prompt=diagnostic_prompt(historical);report['prompt_sha256']=hashlib.sha256(prompt.encode()).hexdigest()
  cell=Cell('astronomy','mesh',1)
  pods=r.kube_json('get','pods','-n',cell.namespace)['items']
  candidates=[p for p in pods if p.get('status',{}).get('phase')=='Running' and any(c['name']=='agent' for c in p.get('spec',{}).get('containers',[]))]
  if len(candidates)!=1:raise RuntimeError('one retained Astronomy agent required')
  pod=candidates[0];r.write(path/'pod-before.json',pod)
  # Save the implementation choosing replay/fallback without printing env secrets.
  code="from pathlib import Path; import json; print(json.dumps({str(p):p.read_text() for p in Path('/app/src').rglob('*.py') if p.name in ('llm.py','agents.py')}))"
  result=r.kube('exec','-n',cell.namespace,pod['metadata']['name'],'-c','agent','--','python3','-c',code,timeout=15,quiet=True)
  r.write(path/'pinned-agent-source.json',json.loads(result.stdout))
  capture=RepairCapture(cell,path,time.time()+max(0,r._DEADLINE-time.monotonic())).start()
  availability=AvailabilityCapture(cell,path,capture).start()
  trace_id=hashlib.sha256(str(path).encode()).hexdigest()[:32]
  run_id=path.parent.name+':agent-diagnosis';request_id=run_id+':prompt:0'
  request={'url':'http://agent:8010/prompt','payload':{'message':prompt},'headers':{
   'Content-Type':'application/json','X-Evaluation-Run':run_id,'X-Evaluation-Request-ID':request_id,
   'traceparent':'00-'+trace_id+'-'+hashlib.sha256(request_id.encode()).hexdigest()[:16]+'-01'}}
  report.update(trace_id=trace_id,run_id=run_id,request_id=request_id)
  r.write(path/'manifest.json',dict(start=r.utc(),run_id=run_id,mode='agent-diagnostic'))
  script='''import urllib.request,urllib.error,json,time,hashlib,sys
r=json.loads(sys.argv[1]);start=time.time_ns();code=0;error=None;body=b''
request=urllib.request.Request(r['url'],json.dumps(r['payload']).encode(),headers=r['headers'],method='POST')
try:
 with urllib.request.urlopen(request,timeout=30) as response:code=response.status;body=response.read(65537)
except urllib.error.HTTPError as exc:code=exc.code;body=exc.read(65537);error=type(exc).__name__
except Exception as exc:error=type(exc).__name__+': '+str(exc)
print(json.dumps(dict(requested_at_ns=start,received_at_ns=time.time_ns(),status=code,exception=error,body_bytes=len(body),body_sha256=hashlib.sha256(body).hexdigest(),traceparent=request.headers.get('Traceparent'))))'''
  result=r.kube('exec','-n',cell.namespace,pod['metadata']['name'],'-c','agent','--','env','OTEL_SDK_DISABLED=true','python3','-c',script,json.dumps(request),timeout=40,quiet=True)
  observed=json.loads(result.stdout);report['attempted_requests']=1;report['response']=observed
  r.write(path/'client-observation.json',observed)
  report['client_observed_at']=r.utc()
  from .pilot import sleep_bounded
  sleep_bounded(min(90,max(0,r._DEADLINE-time.monotonic()-120)))
 except Exception as exc:
  report['state']='incomplete';report['errors'].append(str(exc))
 finally:
  if capture:
   try:
    manifest=json.loads((path/'manifest.json').read_text()) if (path/'manifest.json').exists() else {}
    manifest.update(end=r.utc());r.write(path/'manifest.json',manifest)
   except Exception as exc:report['errors'].append('manifest:'+str(exc))
  from .timing_pilot import stop_captures
  report.update(stop_captures(availability,capture))
  r._DEADLINE=saved
  selected=[]
  for p in (path/'structured/gateway/raw').glob('*.json'):
   for line in p.open():
    for resource in json.loads(line).get('resourceSpans',[]):
     for scope in resource.get('scopeSpans',[]):selected.extend(s for s in scope.get('spans',[]) if s.get('traceId')==report.get('trace_id'))
  report['live_chain']=correlated_failure(selected,report.get('trace_id'))
  report.update(finished_at=r.utc(),interpretation='Single bounded reproduction; original dependency cause remains unresolved unless correlated evidence supports it. No retry or fixture substitution.')
  r.write(path/'report.json',report);r.checksums(path)
 return report
