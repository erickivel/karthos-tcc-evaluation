"""Format verified, frozen results. No scientific rescoring or new measurements."""
import collections,csv,hashlib,io,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];E=ROOT/'evidence'
def read(p):return json.loads(p.read_text())
def save(name,data):(ROOT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def n(x):
 if x is None:return '—'
 if isinstance(x,float):return format(x,'.10g').replace('.',',')
 return str(x)
def table(head,rows):return '\n'.join(['| '+' | '.join(head)+' |','|'+'|'.join(['---']*len(head))+'|']+['| '+' | '.join(n(v).replace('|','/') for v in row)+' |' for row in rows])+'\n'
verdict={'pass':'aprovada','insufficient':'inconclusiva','incomplete':'inconclusiva','not-applicable':'não aplicável','fail':'reprovada'}
r=read(E/'run-report.json');c=read(E/'claim-matrix.json');receipt=read(E/'summary-validation.json');proof={v['file']:v for v in receipt['results']}
seal=dict((line.split('  ',1)[1],line.split('  ',1)[0]) for line in (E/'GKE-SHA256SUMS').read_text().splitlines())
for f in ['run-report.json','claim-matrix.json']:assert hashlib.sha256((E/f).read_bytes()).hexdigest()==seal[f]
assert r['planned_windows']==14 and len(r['windows'])==14 and all(w['evidence_valid'] for w in r['windows'])
labels=[('B1','boutique-traces-r1','Boutique / traces / C1'),('B2','astronomy-traces-r1','Astronomy / traces / C1'),('B3','astronomy-istio-r1','Astronomy / Istio / C1'),('B4','boutique-istio-r1','Boutique / Istio / C1'),('B5','boutique-istio-r2','Boutique / Istio / C2'),('B6','boutique-traces-r2','Boutique / traces / C2')]
data=[];nums=[];families=[];metrics=[];background=[];resource_rows=[]
for bid,label,title in labels:
 p=E/'baselines'/(label+'-summary.json');assert hashlib.sha256(p.read_bytes()).hexdigest()==proof[p.name]['sha256'];d=read(p);q=d['report'];assert q['product_revision']==c['product_revision']
 logical=q['spec'].get('logical_id') or q['spec']['id'].removesuffix('-a1');w=next(w for w in r['windows'] if w['logical_id']==logical)
 assert d['input_archive']['sha256']==w['export']['analysis_export']['parts'][0]['sha256']
 data.append({'baseline':bid,'label':title,'logical_id':logical,'archive':w['export']['analysis_export'],'summary_sha256':proof[p.name]['sha256'],'families':w['families'],'metric_states':q['selected_accuracy'],'numerical_group_counts':{m:{k:v for k,v in row.items() if k!='first_comparison'} for m,row in q['numerical_agreement']['per_metric'].items()}})
 for family,x in w['families'].items():families.append({'baseline':bid,'family':family,'state':x['state'],'checks':x['checks'],**x['counts']})
 for x in q['selected_accuracy']:metrics.append({'baseline':bid,'metric':x['name'],'unit':x['canonical_unit'],'state':x['state'],'reference':x['reference'],'api_storage_state':x.get('storage_api_parity',{}).get('state')})
 bg=q['background_traffic'];background.append({'baseline':bid,**bg['counts']})
 def add(metric,stage,unit,x,path):
  nums.append({'baseline':bid,'metric':metric,'comparison':stage,'unit':unit,**{k:x.get(k) for k in ['reference','observed','absolute_error','relative_error','bound','state']},'source_path':path})
 if 'traces' in label:
  t=q['settled_trace_population']
  for key in ['requests','errors']:
   z=t[key];ref=z['expected'];obs=z['observed'];add(key,'população pareada → armazenamento','count',{'reference':ref,'observed':obs,'absolute_error':abs(obs-ref),'relative_error':abs(obs-ref)/abs(ref) if ref else None,'bound':0,'state':z['state']},'settled_trace_population.'+key)
  for i,x in enumerate(t['duration']['checks']):add('duration','spans pareados → fonte' if i==0 else 'fonte → armazenamento','ms',x,'settled_trace_population.duration.checks['+str(i)+']')
 else:
  t=q['settled_duration_diagnostics'];proofpop=t['population_proof']
  # Count and error agreement retained by the paired cross-run comparison and population proof.
  comp=next(x for x in c['claims'] if x['claim']==('source-independence-astronomy' if label.startswith('astronomy') else 'clean-rebuild-boutique-mesh'))
  idx=1 if label.endswith('r2') else (1 if label.startswith('astronomy') else 0)
  acct=comp['details']['accounting']['per_run'][idx];add('requests','referência de checkout → total selecionado','count',acct,'claim-matrix:'+comp['claim']+'.details.accounting.per_run['+str(idx)+']')
  for key in ['native_settled_to_frozen_store','native_settled_to_declared_drain_export']:
   add('duration','fonte → armazenamento congelado' if key.endswith('frozen_store') else 'fonte → exportação de drain','ms',t[key],'settled_duration_diagnostics.'+key)
  # Error expected values are retained separately; do not invent unrecorded scalar comparisons.
  data[-1]['mesh_population_expected']={k:proofpop[k] for k in ['expected_requests','expected_errors','state']}
 fixed_error=q['thesis_answers']['request-error-numerical-agreement'].get('independent_agreement',{}).get('comparison')
 if fixed_error:add('errors','referência → API na janela fixa','count',fixed_error,'thesis_answers.request-error-numerical-agreement.independent_agreement.comparison')
 checks=q['resources']['checks']
 for metric,unit in [('cpu','cores'),('memory','bytes')]:
  subset=[x for x in checks if x['metric']==metric];values=[x['independent_agreement'] for x in subset if isinstance(x.get('independent_agreement'),dict) and 'absolute_error' in x['independent_agreement']]
  assert subset and values
  first=next(x for x in subset if 'absolute_error' in x.get('independent_agreement',{}));add('container.'+metric,'primeira comparação registrada','cores' if metric=='cpu' else 'bytes',first['independent_agreement'],'resources.checks['+str(checks.index(first))+'].independent_agreement')
  resource_rows.append([bid,metric,len(subset),len(values),max(x['absolute_error'] for x in values),unit])
  data[-1].setdefault('resource_comparisons',{})[metric]={'planned':len(subset),'evaluable':len(values),'states':dict(collections.Counter(x['state'] for x in subset)),'maximum_absolute_error':max(x['absolute_error'] for x in values),'first_comparison':first}
assert len(data)==6
save('resultados.json',{'run_id':'thesis-robustness-full-20261119-v2','no_rescoring':True,'claims':[ {k:v for k,v in x.items() if k not in ['baselines','checks','details','observed']} for x in c['claims']],'baselines':data,'numerical_results':nums,'family_results':families,'selected_metrics':metrics,'background':background})
for filename,rows in [('resultados-numericos.csv',nums),('familias.csv',families),('metricas-selecionadas.csv',metrics),('trafego-observado.csv',background)]:
 with (ROOT/filename).open('w',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator="\n");writer.writeheader();writer.writerows(rows)
fp=read(E/'footprint-observation.json')['observation'];fprows=[]
for w in fp['windows']:
 bid=next(x['baseline'] for x in data if x['logical_id']==w['window']);assert w['seal_sha256']==w['export']['original_window_seal_sha256']
 for component,v in sorted(w['components'].items()):fprows.append([bid,component,v['containers'],v['cpu_cores'],v['memory_bytes'],w['timestamps']['cpu_cores']])
print(json.dumps({'baselines':len(data),'numerical_rows':len(nums),'families':len(families),'metric_rows':len(metrics),'claims':dict(collections.Counter(x['verdict'] for x in c['claims']))}))
