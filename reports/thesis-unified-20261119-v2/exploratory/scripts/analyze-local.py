"""Post-run exploration of verified local summaries; no rescoring."""
import json,hashlib,datetime
from pathlib import Path
O=Path(__file__).resolve().parents[1];R=O.parent;P=Path('/Users/kivel/Desktop/Projects/tcc/karthos')
def read(p):return json.loads(p.read_text())
labels=['boutique-traces-r1','astronomy-traces-r1','astronomy-istio-r1','boutique-istio-r1','boutique-istio-r2','boutique-traces-r2'];rows=[];inputs=[]
for i,label in enumerate(labels,1):
 p=R/'evidence/baselines'/(label+'-summary.json');q=read(p)['report'];inputs.append({'path':str(p.relative_to(R)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
 b=next(x for x in q['boundary_summary']['boundaries'] if x['boundary']=='storage-to-api');assert b.get('omitted_examples',0)==0
 rows.append({'baseline':'B'+str(i),'window':[q['manifest']['start'],q['manifest']['end']],'counter_priming':{k:v for k,v in q['manifest'].get('counter_priming',{}).items() if k in ['started_at','finished_at','state','requests','included_in_measurement']},'fixed_requests':sum(x['observed'] for x in b['examples']),'request_partitions':b['examples'],'fixed_errors':q['thesis_answers']['request-error-numerical-agreement']['independent_agreement']['comparison'],'counter_brackets':q['boundary_summary']['storage_counter_brackets'],'host_reasons':{k:x['reasons'] for k,x in q['numerical_agreement']['per_metric'].items() if k.startswith('system.')},'background':q['background_traffic']['counts']})
h=read(R/'evidence/run-report.json')['history'];l=h['lifecycle_observation'];history={'restart':h['restart'],'independent_reference_acquisition':l['independent_reference_acquisition'],'first_matching_api_acquisition':l['first_matching_api_acquisition'],'first_matching_snapshot':l['first_matching_snapshot'],'reference_to_api_acquisition_offset_seconds':l['reference_to_api_acquisition_offset_seconds'],'trigger_to_first_verified_snapshot_seconds':l['trigger_to_first_verified_snapshot_seconds'],'polls':l['polls'],'unavailable_polls':l['unavailable_polls'],'exact_transition_time_established':False}
source=[]
for rel in ['pkg/adapters/storage/victoriametrics/metric_store.go','pkg/sources/servicegraph/raw_metrics.go','pkg/sources/istio/query_view.go','pkg/backend/ingest/normalizer.go','cmd/backend/main.go','pkg/adapters/otel/compiler.go']:
 p=P/rel;source.append({'revision':'4c2861b539fcd24d6a458dc0fd545fbdda5470d5','file':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
p=R.parents[1]/'evaluation/gke/report_summary.py';source.append({'file':'evaluation/gke/report_summary.py','sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
result={'classification':'post-run exploratory; original sealed verdicts unchanged','status':'partial; raw transfer approval pending','inputs':inputs,'code':source,'baselines':rows,'history':history,'not_established':['which individual errors fall outside VM window','same-window arbitration durations','60 second host means','detailed state transition sequence','component interval means and maxima','unclassified observer route attribution']}
(O/'local-findings.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'fixed_requests':[x['fixed_requests'] for x in rows],'fixed_errors':[x['fixed_errors']['observed'] for x in rows]}))
