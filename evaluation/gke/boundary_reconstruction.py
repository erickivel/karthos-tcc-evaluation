"""Reconstruct a trace error counter boundary from sealed source and storage evidence."""
import argparse,hashlib,json,tarfile,tempfile
from pathlib import Path
from .models import sha,write
from ..timing_analysis import trace_events
from ..scoring import stamp

def counter_boundaries(points,start,end):
 if not start<end:raise ValueError('invalid workload bounds')
 ordered=sorted(points,key=lambda p:p['timestamp_ns'])
 before=[p for p in ordered if p['timestamp_ns']/1e9<=start]
 inside=[p for p in ordered if start<p['timestamp_ns']/1e9<=end]
 after=[p for p in ordered if p['timestamp_ns']/1e9>end]
 if not before or not inside or not after:raise ValueError('missing counter boundary evidence')
 baseline=before[-1];last=inside[-1];tail=after[0]
 relevant=[baseline,*inside,tail]
 if any(b['value']<a['value'] or (b['timestamp_ns']==a['timestamp_ns'] and b['value']!=a['value']) for a,b in zip(relevant,relevant[1:])):
  raise ValueError('counter reset or contradictory timestamp requires separate handling')
 return baseline,last,tail

def reconstruct(archive,expected,destination):
 archive=Path(archive);destination=Path(destination)
 if destination.exists():raise ValueError('new destination required')
 if sha(archive)!=expected:raise ValueError('archive checksum differs')
 used={}
 with tarfile.open(archive) as tar:
  seals=dict((line.split('  ',1)[1],line.split('  ',1)[0]) for line in tar.extractfile('GKE-SHA256SUMS').read().decode().splitlines())
  def raw(name):
   b=tar.extractfile(name).read()
   if hashlib.sha256(b).hexdigest()!=seals[name]:raise ValueError('member checksum differs: '+name)
   used[name]=seals[name];return b
  manifest=json.loads(raw('manifest.json'));start,end=map(stamp,(manifest['start'],manifest['end']))
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'structured/gateway/raw';p.mkdir(parents=True)
   (p/'traces.json').write_bytes(raw('structured/gateway/raw/traces.json'))
   traces,conflicts=trace_events(Path(tmp))
  failures=[r for r in traces if r['paired_frontend'] and r['connector_failed'] and start<=r['start']<=r['completion']<end]
  points=[]
  for line in raw('structured/gateway/raw/metrics.json').splitlines():
   for resource in json.loads(line).get('resourceMetrics',[]):
    for scope in resource.get('scopeMetrics',[]):
     for metric in scope.get('metrics',[]):
      if metric['name']!='traces_service_graph_request_failed_total':continue
      for dp in metric.get('sum',{}).get('dataPoints',[]):
       attrs={a['key']:next(iter(a['value'].values())) for a in dp.get('attributes',[])}
       if attrs.get('client')=='frontend' and attrs.get('server')=='checkoutservice' and attrs.get('client_k8s.namespace.name')==manifest['namespace']:
        points.append({'timestamp_ns':int(dp['timeUnixNano']),'value':float(dp.get('asDouble',dp.get('asInt'))),'attributes':attrs})
  points.sort(key=lambda p:p['timestamp_ns'])
  stored=[]
  for line in raw('extended/vm-raw.ndjson').splitlines():
   row=json.loads(line);labels=row.get('metric',{})
   if labels.get('__name__')=='servicegraph_request_error_count' and labels.get('edge_client')=='frontend' and labels.get('edge_server')=='checkoutservice' and labels.get('client_namespace')==manifest['namespace']:stored.append(row)
  baseline,last,after=counter_boundaries(points,start,end)
  query_manifest=json.loads(raw('intervals/whole/vm-parity/manifest.json'))
  query=next(q for q in query_manifest['queries'] if q['api_file']=='api-error-count.json')
  ledger=json.loads(raw('consumer/ledger.json'));samples=ledger['answers']['request-error-derivation']['value']['samples']
  api=sum(x['value'] for x in samples)
  report={'version':'trace-boundary1','archive_sha256':expected,'inputs':used,'window':[start,end],
   'independent_failed_calls':failures,'trace_conflicts':conflicts,'raw_counter_points':points,'stored_series':stored,'query':query,
   'baseline':baseline,'last_in_window':last,'first_after_window':after,'api_value':api,
   'raw_fixed_delta':last['value']-baseline['value'],'raw_settled_delta':after['value']-baseline['value'],
   'interpretation':'The error increment is already timestamped after the evaluation end in raw connector output. Storage/API preserves that boundary; waiting longer does not move a fixed historical query endpoint.',
   'limitation':'Counter increments have no trace IDs. Do not assign individual late increments to specific calls or infer exact connector receipt times from span event times.',
   'original_verdict_changed':False}
 destination.mkdir(parents=True);write(destination/'reconstruction.json',report)
 write(destination/'vm-import.ndjson',''.join(json.dumps(r)+'\n' for r in stored))
 return report

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive');p.add_argument('destination');p.add_argument('--sha256',required=True);a=p.parse_args();r=reconstruct(a.archive,a.sha256,a.destination);print(json.dumps({k:r[k] for k in ('api_value','raw_fixed_delta','raw_settled_delta')}))
