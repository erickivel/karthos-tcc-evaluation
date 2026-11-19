import json,hashlib,collections,sys
from pathlib import Path
root=Path(sys.argv[1]);out=Path(sys.argv[2]);results=[]
for p in sorted(root.glob('*-summary.json')):
 source=p.with_name(p.name.replace('-summary.json','-projection.json'));raw=source.read_bytes();original=json.loads(raw);summary=json.loads(p.read_bytes());a=original['report'];b=summary['report']
 assert summary['derived_projection_sha256']==hashlib.sha256(raw).hexdigest()
 for k in original:
  if k!='report':assert original[k]==summary[k],k
 for k in a:
  if k not in ('numerical_agreement','selected_accuracy'):assert a[k]==b[k],k
 checks=a['numerical_agreement']['checks'];groups=collections.defaultdict(list)
 for i,c in enumerate(checks):groups[c.get('metric') or c.get('observed',{}).get('metric') or 'unidentified'].append((i,c))
 assert set(groups)==set(b['numerical_agreement']['per_metric'])
 for metric,items in groups.items():
  g=b['numerical_agreement']['per_metric'][metric]
  assert g['comparisons']==len(items)
  assert g['states']==dict(collections.Counter(c.get('state','missing') for _,c in items))
  assert g['reasons']==dict(collections.Counter(c['reason'] for _,c in items if c.get('reason')))
  assert g['first_comparison_index']==items[0][0] and g['first_comparison']==items[0][1]
 assert len(a['selected_accuracy'])==len(b['selected_accuracy'])
 for original_metric,summary_metric in zip(a['selected_accuracy'],b['selected_accuracy']):
  for k in original_metric:
   if k!='storage_api_parity':assert original_metric[k]==summary_metric[k],k
  left=original_metric.get('storage_api_parity');right=summary_metric.get('storage_api_parity')
  if isinstance(left,dict) and isinstance(left.get('checks'),list):
   for k in left:
    if k!='checks':assert left[k]==right[k]
   rows=left['checks'];errors=[c['absolute_error'] for c in rows if isinstance(c.get('absolute_error'),(int,float))]
   assert right['retained_comparison_count']==len(rows)
   assert right['retained_comparison_states']==dict(collections.Counter(c.get('state','missing') for c in rows))
   assert right['first_comparison']==(rows[0] if rows else None)
   assert right['maximum_absolute_error']==(max(errors) if errors else None)
  else:assert left==right
 results.append({'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'source_projection_sha256':hashlib.sha256(raw).hexdigest(),'verified':True,'comparisons_accounted':len(checks),'metric_groups':len(groups)})
receipt={'method':'compare compact summaries against verified full projections: unchanged fields, every metric/state/reason count, first recorded comparisons, API parity counts and max absolute errors','results':results}
with out.open('x') as f:json.dump(receipt,f,indent=2);f.write('\n')
print(json.dumps(receipt))
