"""Verify every sealed member; extract a bounded allowlist for offline exploration."""
import sys,json,tarfile,hashlib,shutil
from pathlib import Path
BASE=Path(__import__('os').environ.get('KARTHOS_ANALYSIS_BASE','/srv/karthos-analysis')+'/exploratory')
for label in sys.argv[1:]:
 src=BASE/'inputs'/(label+'.tar.gz');out=BASE/'selected'/label;out.mkdir(parents=True,exist_ok=False);checked=[];selected=[]
 with tarfile.open(src) as tar:
  members=tar.getmembers();assert len({m.name for m in members})==len(members)
  seal=tar.extractfile('GKE-SHA256SUMS').read();seals={n:h for h,n in (l.split('  ',1) for l in seal.decode().splitlines())}
  assert {m.name for m in members if m.isfile() and m.name!='GKE-SHA256SUMS'}==set(seals)
  for m in members:
   assert not Path(m.name).is_absolute() and '..' not in Path(m.name).parts and (m.isfile() or m.isdir())
   if not m.isfile():continue
   choose=m.name in ['manifest.json','ledger.ndjson','api-nodes.json','api-edges.json','api-cpu.json','api-memory.json','api-request-count.json','api-error-count.json','vm-raw.ndjson','extended/vm-raw.ndjson','vm-parity/manifest.json','vm-parity/api-request-count.json','vm-parity/api-error-count.json','selection/profile.json','selection/plan.json','selection/karthos-gateway-config.json','selection/karthos-agent-config.json','k8s-pods.json','karthos-pods.json','quiet-before.json','quiet-after.json','setup-effective-settings.json','setup-running-image-verification.json','setup-generated-collector-contract.json','structured/gateway/raw/traces.json','verification.json','timing-verification.json'] or (m.name.startswith('logs/') and m.name.endswith('__eval-observer.log')) or (label=='B1' and m.name.startswith('references/') and m.name.endswith('.ndjson')) or (label=='arbitration' and 'raw/traces' in m.name)
   h=hashlib.sha256();dest=None
   if choose:
    assert m.size<2*1024**3;dst=out/m.name;dst.parent.mkdir(parents=True,exist_ok=True);dest=dst.open('xb')
   with tar.extractfile(m) as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):
     h.update(chunk)
     if dest:dest.write(chunk)
   if dest:dest.close()
   if m.name!='GKE-SHA256SUMS':assert h.hexdigest()==seals[m.name],m.name
   row={'name':m.name,'bytes':m.size,'sha256':h.hexdigest()};checked.append(row)
   if choose:selected.append(row)
 receipt={'label':label,'archive_sha256':hashlib.file_digest(src.open('rb'),'sha256').hexdigest(),'seal_sha256':hashlib.sha256(seal).hexdigest(),'verified_members':len(checked),'selected':selected}
 (out/'EXTRACTION.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'label':label,'verified_members':len(checked),'selected_bytes':sum(r['bytes'] for r in selected)}),flush=True)
