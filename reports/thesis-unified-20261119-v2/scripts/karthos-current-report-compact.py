import json,sys,hashlib,collections
from pathlib import Path
source=Path(sys.argv[1]);target=Path(sys.argv[2])
if target.exists():raise ValueError('destination exists')
raw=source.read_bytes();d=json.loads(raw);r=d['report']
checks=r.get('numerical_agreement',{}).get('checks',[]);groups={}
for i,c in enumerate(checks):
 metric=c.get('metric') or c.get('observed',{}).get('metric') or 'unidentified'
 g=groups.setdefault(metric,{'comparisons':0,'states':{},'reasons':{},'first_comparison_index':i,'first_comparison':c})
 g['comparisons']+=1;s=c.get('state','missing');g['states'][s]=g['states'].get(s,0)+1
 reason=c.get('reason')
 if reason:g['reasons'][reason]=g['reasons'].get(reason,0)+1
r['numerical_agreement']={**{k:v for k,v in r.get('numerical_agreement',{}).items() if k!='checks'},'per_metric':groups,'checks_location':'gke-evaluation.json:numerical_agreement.checks; compact comparison references resolved by frozen evaluator'}
for item in r.get('selected_accuracy',[]):
 parity=item.get('storage_api_parity')
 if isinstance(parity,dict) and isinstance(parity.get('checks'),list):
  original=parity.pop('checks');parity['retained_comparison_count']=len(original);parity['retained_comparison_states']=dict(collections.Counter(c.get('state','missing') for c in original));parity['first_comparison']=original[0] if original else None
  errors=[c['absolute_error'] for c in original if isinstance(c.get('absolute_error'),(int,float))]
  parity['maximum_absolute_error']=max(errors) if errors else None
  parity['checks_location']='gke-evaluation.json:selected_accuracy[name='+str(item.get('name'))+'].storage_api_parity.checks'
d['derived_projection_sha256']=hashlib.sha256(raw).hexdigest();d['report']=r
d['compact_summary_scope']='No rescoring. All numerical comparison states counted by metric, first recorded comparison shown without selection by outcome. Full values, references, units, errors and tolerances remain in indexed sealed analysis archive. API parity arrays counted with maximum absolute error.'
encoded=json.dumps(d,indent=2,sort_keys=True)+'\n'
with target.open('x') as f:f.write(encoded)
print(json.dumps({'bytes':len(encoded.encode()),'metrics':len(groups),'comparisons':sum(g['comparisons'] for g in groups.values()),'output':str(target)}))
