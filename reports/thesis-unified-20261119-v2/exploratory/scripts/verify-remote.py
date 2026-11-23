"""Verify retained history seal, stable host identities, and owned replay cleanup."""
import pathlib,hashlib,json,subprocess
p=pathlib.Path('/home/kivel/projects/karthos-evaluation-authorization-20261119/exploratory');h=p/'inputs/history';rows=[]
for line in (h/'GKE-SHA256SUMS').read_text().splitlines():
 digest,name=line.split(maxsplit=1);f=h/name
 assert f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==digest,name
 rows.append({'path':name,'sha256':digest})
left=subprocess.check_output(['docker','ps','-a','--filter','name=karthos-exploratory-replay-','--format','{{.Names}}'],text=True).strip();assert not left
(p/'outputs/final-verification.json').write_text(json.dumps({'history_sealed_members_verified':rows,'owned_replay_containers_remaining':[],'scope':'offline investigation resources only; cloud closeout remains in original report'},indent=2)+'\n')
boots={}
for f in (p/'selected/B1/references').glob('*.ndjson'):
 identities=set()
 for line in f.open():
  x=json.loads(line);identities.add((x['node'],x['node_uid'],x['boot_id']))
 assert len(identities)==1
 boots[f.name]=list(identities)
(p/'outputs/host-identities.json').write_text(json.dumps(boots,indent=2)+'\n')
