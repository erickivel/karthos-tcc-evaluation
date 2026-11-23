"""Validate derived invariants and preserve original sealed report."""
import json,pathlib,hashlib,math
P=pathlib.Path(__file__).resolve().parents[1];checks=[]
def check(name,value):
 assert value,name
 checks.append(name)
def read(name):return json.loads((P/name).read_text())
acq={x['file']:x for x in read('acquisition.json')['objects']}
expected_errors=[56,56,59,57,58,56];expected_requests=[339,338,354,345,350,336]
replay=read('raw-derived/replay.json')['results'];dur=read('raw-derived/durations.json')
for i in range(6):
 label=f'B{i+1}';d=read('raw-derived/'+label+'.json');s=d['E1']['error_series'][0];r=replay[i]
 check(label+' archive seal hash matches acquisition',d['extraction']['archive_sha256']==acq[label+'.tar.gz']['sha256'])
 check(label+' preexisting series',s['first_retained_sample'][0]<d['E1']['window'][0])
 check(label+' errors and zero resets',d['E1']['generator_error_events']==60 and s['resets']==0 and s['simple_pre_to_last_delta']==expected_errors[i])
 check(label+' tail accounting',s['errors_completed_after_last_sample']+s['completed_errors_not_yet_in_counter_at_last_sample']==60-expected_errors[i])
 check(label+' settled total',s['first_sample_reaching_all_errors'][1]-s['pre_window_sample'][1]==60)
 check(label+' replay requests',next(q['checkout_total'] for q in r['queries'] if q['query']['api_file']=='api-request-count.json')==expected_requests[i])
 check(label+' replay errors',next(q['checkout_total'] for q in r['queries'] if q['query']['api_file']=='api-error-count.json')==expected_errors[i])
 check(label+' matched spans',d['E2']['pairs']==360 and d['E2']['conflicts']==0)
 check(label+' histogram population',dur[i]['count']==360)
 check(label+' unknown grouping',sum(g['count'] for g in d['E6']['unknown_groups'])==d['E6']['unknown_count'])
 check(label+' resource coverage',all(x['samples']==10 and 269<x['covered_span_seconds']<271 for x in d['E5']))
check('arbitration 72 pairs',dur[-1]['count']==72 and read('raw-derived/arbitration.json')['E2']['pairs']==72)
rows=read('raw-derived/host-60s.json')['rows'];check('45 paired host bins',len(rows)==45 and all(x['reference_samples'] and x['observed_samples'] and math.isfinite(x['relative_error_percent']) for x in rows))
h=read('raw-derived/history.json');check('history sample counts',len(h['references'])==3 and len(h['api_polls'])==61)
check('old relation absent after',len(h['old_runs_on_before'])==1 and not h['old_runs_on_after'] and not h['old_nodes_after'])
original=[]
for line in (P.parent/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split(maxsplit=1);name=name.lstrip('*');f=P.parent/name
 check('original seal '+name,hashlib.sha256(f.read_bytes()).hexdigest()==digest);original.append(name)
(P/'validation.json').write_text(json.dumps({'status':'passed','checks':checks,'original_files_verified':len(original),'scope':'consistency only; not new scientific verdicts'},indent=2)+'\n')
print(len(checks),'checks passed;',len(original),'original sealed files unchanged')
