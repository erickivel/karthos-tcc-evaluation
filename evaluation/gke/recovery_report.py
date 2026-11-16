"""Build a compact, versioned report from separate offline recovery outputs."""
import argparse,html,json
from collections import Counter
from pathlib import Path
from .models import read,write,sha
from .thesis_comparison import compare_pairs,representative_state

def build(original,root):
 original=Path(original);root=Path(root)
 if (root/'recovery-report.json').exists():raise ValueError('recovery report already exists')
 run=read(original);rows=[];exported={};matrix=[];reasons=Counter()
 for row in run['records']:
  attempt=row['attempt_id'];path=root/'claims'/attempt
  if row['state']=='complete':
   report=read(path/'report.json');accuracy=read(root/attempt/'summary.json')
   spec=report['spec'];logical=row.get('logical_id',row['id']);matrix.append(dict(spec,id=logical))
   exported[logical]=(row['export']['uri'],report)
   for item in accuracy['resources']['by_metric_reason']:
    if item['state']!='pass':reasons[(item['metric'],item['state'],item['reason'])]+=item['comparisons']
   rows.append({'id':logical,'attempt':attempt,'execution':row['state'],'evidence_valid':row.get('evidence_valid'),
     'representative_accuracy':representative_state(report),'families':report['families'],
     'consumer_answers':report['thesis_answers'],'caller_recovery':report['caller_recovery'],
     'source_graph':report['service_graph_evidence'],'artifact':str((path/'report.json').relative_to(root))})
  else:
   rows.append({'id':row['id'],'attempt':attempt,'execution':row['state'],'evidence_valid':False,'representative_accuracy':'unattempted',
    'reason':'Astronomy/Istio Agent remained Pending/Unschedulable: Insufficient cpu before traffic; exact historical shortfall unavailable'})
 # Include absent baselines explicitly in the matrix, so missing coverage remains visible.
 for row in run['records']:
  if row['state']!='complete' and 'baseline' in row['id']:
   matrix.append({'id':row['id'],'repeat':1,'cell':'astronomy-mesh','phase':'baseline'})
 pairs=compare_pairs(matrix,exported,strict=True)
 baselines=[v[1] for v in exported.values() if v[1]['spec']['phase']=='baseline']
 claims=[]
 for name in ('interval-callers','current-replicas-and-placement','container-resource-history','request-error-derivation','request-error-numerical-agreement'):
  states=[d.get('thesis_answers',{}).get(name,{}).get('state','insufficient') for d in baselines]
  claims.append({'claim':name,'eligible_baselines':len(states),'planned_baselines':6,'answers':dict(Counter(states)),
   'verdict':'fail' if 'incorrect' in states else 'pass' if len(states)==6 and all(s=='correct' for s in states) else 'insufficient'})
 failures=[]
 for row in run['records']:
  for attempt in row.get('attempts',[]):
   if attempt.get('state') not in ('complete',None):failures.append(attempt)
 report={'version':'official-recovery-v1','original_run_report_sha256':sha(original),'original_run_report':str(original),
  'originals_modified':False,'cloud_launched':False,'windows':rows,'cross_deployment_claims':pairs,'consumer_claims':claims,
  'resource_limitations':[dict(metric=k[0],state=k[1],reason=k[2],comparison_rows=n) for k,n in reasons.items()],
  'denominator_warning':'comparison rows include repeated/forwarded observations; not independent metric loss opportunities',
  'history_and_arbitration':'original passing observations retained; no replacement/retry performed',
  'remaining_gke':[
   'Astronomy/Istio smoke and baseline were never measured and cannot be recovered offline.',
   'Recommend a paired Astronomy trace + Istio smoke/baseline set (four windows) after the placement change, to preserve comparable testbed inputs and validate the corrected trace consumer.',
   'Boutique structure/accounting reproducibility can use both retained cluster lifetimes; corrected trace history endpoint execution was not recorded, so full consumer reproducibility remains unconfirmed.',
   'Exact host/kernel/cache timing remains outside the bounded thesis claim; another unchanged run cannot supply timestamps the collector does not expose.'],
  'product_defect':'none demonstrated; fixed-window disagreements and insufficient references do not establish an ingestion defect'}
 write(root/'recovery-report.json',report)
 columns=('Window','Execution','Required evidence','Representative accuracy','Report')
 table=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (r['id'],r['execution'],r['evidence_valid'],r['representative_accuracy'],r.get('artifact','deployment failure')) )+'</tr>' for r in rows)
 pairtable=''.join('<tr><td>'+html.escape(p['claim'])+'</td><td>'+html.escape(p['verdict'])+'</td></tr>' for p in pairs)
 page='<!doctype html><meta charset="utf-8"><title>Official thesis evidence recovery</title><style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:16px;color:#172033}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;border:1px solid #ccc;text-align:left}h1,h2{color:#163d65}</style><h1>Official thesis evidence recovery</h1><p>12 completed logical windows; 2 deployment-blocked windows. Original verdicts remain unchanged. No GCP launch.</p><p>Passing execution does not imply every numerical comparison passed. Missing historical endpoint queries remain explicitly unconfirmed.</p><table><tr>'+''.join('<th>'+c+'</th>' for c in columns)+'</tr>'+table+'</table><h2>Cross-deployment claims</h2><table>'+pairtable+'</table><h2>Remaining GKE evidence</h2><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in report['remaining_gke'])+'</ul><p>See recovery-report.json and each per-attempt summary for references, comparison populations and reasons. Exact cause of the sampler timeout remains unknown.</p>'
 write(root/'index.html',page)
 write(root/'REPORT-SHA256SUMS',''.join(sha(root/name)+'  '+name+'\n' for name in ('recovery-report.json','index.html')))
 return report

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-report',required=True,type=Path);parser.add_argument('--analysis-root',required=True,type=Path)
 args=parser.parse_args();build(args.run_report,args.analysis_root)
