"""Build a local index of immutable source references; no cloud mutations."""
from pathlib import Path
import json,hashlib
R=Path(__file__).resolve().parents[1];E=R/'evidence'
def read(p):return json.loads(p.read_text())
r=read(E/'run-report.json');c=read(E/'claim-matrix.json');m=read(E/'evidence-metadata-verification.json');b=read(R/'resultados.json')
rows=[]
for i,w in enumerate(r['windows'],1):
 e=w['export'];a=e['analysis_export'];rows.append({'window':i,'logical_id':w['logical_id'],'attempt_id':w['attempt_id'],'state':w['state'],'evidence_valid':w['evidence_valid'],'raw_projection':{'uri':e['uri'],'parts':e['objects'],'original_window_seal_sha256':e['original_window_seal_sha256']},'analysis':{'uri':a['uri'],'parts':a['parts'],'members':a['members']}})
local=[]
for p in sorted(R.rglob('*')):
 if p.is_file() and p.name not in ['INDICE-EVIDENCIAS.md','evidence-index.json','SHA256SUMS','package-validation.json']:
  local.append({'path':str(p.relative_to(R)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
index={'run_id':m['run_id'],'scope':'Index derived from sealed run report, verified analysis summaries and provider metadata; raw payloads remain in evidence storage. No replacement of original results.','primary_bucket':'karthos-karthos-eval-evidence','secondary_bucket':'karthos-karthos-eval-evidence-backup','windows':rows,'history':r['history']['export'],'research_objects':m['objects'],'closure':read(E/'closure-readback.json'),'local_files':local}
(R/'evidence-index.json').write_text(json.dumps(index,indent=2)+'\n')

