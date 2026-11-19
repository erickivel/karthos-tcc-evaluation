"""Check report artifacts against sealed input results without rescoring."""
from pathlib import Path
import json,hashlib,collections,re,csv
R=Path(__file__).resolve().parents[1];E=R/'evidence';checks=[]
def read(p):return json.loads(p.read_text())
def check(condition,name):
 if not condition:raise AssertionError(name)
 checks.append(name)
sums={line.split('  ',1)[1]:line.split('  ',1)[0] for line in (E/'GKE-SHA256SUMS').read_text().splitlines()}
for f in ['run-report.json','claim-matrix.json']:check(hashlib.sha256((E/f).read_bytes()).hexdigest()==sums[f],f+' original seal')
completion=read(E/'completion-request.json')
seal_ref=next(x for x in completion['export_receipt']['objects'] if x['object'].endswith('/GKE-SHA256SUMS'))
check(hashlib.sha256((E/'GKE-SHA256SUMS').read_bytes()).hexdigest()==seal_ref['sha256'],'original seal bound to completion request')
run=read(E/'run-report.json');claims=read(E/'claim-matrix.json');result=read(R/'resultados.json')
check(len(run['windows'])==14 and all(w['evidence_valid'] and w['attempt_count']==1 for w in run['windows']),'14 valid windows, one attempt each')
check(collections.Counter(x['verdict'] for x in claims['claims'])=={'pass':24,'not-applicable':1},'claim counts')
check(len(result['baselines'])==6 and len(result['selected_metrics'])==108,'six baselines and 18 selected metrics each')
for item in read(E/'summary-validation.json')['results']:
 p=E/'baselines'/item['file'];check(hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],item['file']+' verified summary hash')
projections=[read(p)['report'] for p in sorted((E/'baselines').glob('*.json'))]
check(all(x['verifier_implementation_hashes']==projections[0]['verifier_implementation_hashes'] for x in projections),'same frozen verifier implementation hashes across six baselines')
check(all(x['spec']['duration_seconds']==300 for x in projections),'six 300 second baselines')
for row in result['numerical_results']:
 check(row['absolute_error'] is not None and abs(abs(row['observed']-row['reference'])-row['absolute_error'])<1e-8,'numerical absolute error '+row['baseline']+'/'+row['metric']+'/'+row['comparison'])
 if row['reference']!=0:check(abs(row['relative_error']-row['absolute_error']/abs(row['reference']))<1e-8,'relative error '+row['baseline']+'/'+row['metric'])
 else:check(row['relative_error'] is None,'zero reference has undefined relative error')
for row in result['background']:check(row['observed']==row['journey_requests']+row['identified_probe_requests']+row['unclassified_requests'],'background partition '+row['baseline'])
for row in read(E/'closure-readback.json')['objects']:check(hashlib.sha256((E/row['file']).read_bytes()).hexdigest()==row['sha256'],'closure receipt hash '+row['file'])
backup=read(E/'backup-receipt.json');cleanup=read(E/'cleanup-receipt.json');meta=read(E/'evidence-metadata-verification.json')
check(backup['verified'] and not backup['errors'],'backup receipt')
check(meta['verified'] and meta['objects_per_copy']==498 and meta['bytes_per_copy']==1991739650,'independent metadata verification')
check(cleanup['compute_network_absence_verified'] and cleanup['lease_released'] and cleanup['scheduler_paused'] and not cleanup['residual'] and not cleanup['errors'],'cleanup scope')
check(not run['cleanup']['cluster_cleanup']['residual'],'cluster cleanup report')
report=(R/'RELATORIO.md').read_text();check((R/'RESULTADOS.md').read_text() in report,'generated tables incorporated exactly')
for token in ['relatório em andamento','ainda não é o resultado final','a segunda implantação deve']:check(token not in report.lower(),'no stale draft wording: '+token)
for doc in ['RELATORIO.md','INDICE-EVIDENCIAS.md']:
 for target in re.findall(r'\]\(([^)]+)\)',(R/doc).read_text()):
  if '://' not in target and not target.startswith('#'):check((R/target.split('#')[0]).exists(),'local link '+target)
index=read(R/'evidence-index.json')
for row in index['local_files']:check(hashlib.sha256((R/row['path']).read_bytes()).hexdigest()==row['sha256'],'index hash '+row['path'])
check(len(index['windows'])==14 and len(index['research_objects'])==498,'evidence index population')
receipt={'verified':True,'checks_passed':len(checks),'scope':'local report/source hashes, six baseline projections, numerical arithmetic, background partitions, backup/cleanup receipt fields, links and index; no new measurements or scientific rescoring','checks':checks}
(R/'package-validation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'verified':True,'checks_passed':len(checks)}))
