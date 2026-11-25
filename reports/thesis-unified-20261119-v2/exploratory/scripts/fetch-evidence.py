"""Authorized generation-pinned evidence transfer to the analysis server; credentials stay local."""
import hashlib,json,os,subprocess,sys,time,shlex
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];sys.path.insert(0,str(ROOT))
from evaluation.gke.secondary_backup import GCS
OUT=Path(__file__).resolve().parents[1];BASE=__import__('os').environ.get('KARTHOS_ANALYSIS_BASE','/srv/karthos-analysis')+'/exploratory'
env=dict(os.environ,CLOUDSDK_CONFIG=str(ROOT/'.work/gke/gcloud'),CLOUDSDK_CORE_ACCOUNT=__import__('os').environ['CLOUDSDK_CORE_ACCOUNT'],CLOUDSDK_CORE_PROJECT='karthos')
class C:
 def command(self,args,**kw):return subprocess.run(args,env=env,check=True,capture_output=True,text=True,timeout=60)
store=GCS(C(),time.time()+1800);run=json.loads((OUT.parent/'evidence/run-report.json').read_text());bucket='karthos-karthos-eval-evidence';prefix='gke-qualification-v1/runs/thesis-robustness-full-20261119-v2/'
rows={r['name']:r for r in store.list(bucket,prefix)};items=[]
for i,label in [(1,'B1'),(3,'B2'),(5,'B3'),(7,'B4'),(11,'B5'),(13,'B6'),(8,'arbitration')]:
 e=run['windows'][i]['export'];items.append((label+'.tar.gz',e['uri'].split(bucket+'/',1)[1]+'window.tar.gz',e['archive_sha256']))
h=run['history']['export']
for name,meta in h['objects'].items():items.append(('history/'+name,h['uri'].split(bucket+'/',1)[1]+name,meta['sha256']))
receipt=[]
for name,key,expected in items:
 row=rows[key];assert int(row['size'])<600*1024**2
 code="import pathlib,sys,hashlib; p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True); f=p.open('xb');h=hashlib.sha256();n=0\nwhile True:\n b=sys.stdin.buffer.read(1048576)\n if not b:break\n f.write(b);h.update(b);n+=len(b)\nf.close();assert h.hexdigest()==sys.argv[2];print(n)"
 cmd='python3 -c '+shlex.quote(code)+' '+shlex.quote(BASE+'/inputs/'+name)+' '+shlex.quote(expected)
 proc=subprocess.Popen(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',__import__('os').environ.get('KARTHOS_ANALYSIS_HOST','analysis-server'),cmd],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 digest=hashlib.sha256();size=0
 try:
  with store.request('GET',store.object_url(bucket,key),{'alt':'media','generation':row['generation']}) as response:
   while True:
    chunk=response.read(1024*1024)
    if not chunk:break
    proc.stdin.write(chunk);digest.update(chunk);size+=len(chunk)
  proc.stdin.close();proc.stdin=None;stdout,stderr=proc.communicate(timeout=60)
  if proc.returncode:raise RuntimeError(stderr.decode()[:800])
  assert digest.hexdigest()==expected and size==int(row['size'])
 except:proc.kill();raise
 receipt.append({'file':name,'uri':'gs://'+bucket+'/'+key,'generation':row['generation'],'sha256':expected,'bytes':size,'remote_path':BASE+'/inputs/'+name})
 (OUT/'acquisition.json').write_text(json.dumps({'scope':'Authorized transfer; exploratory only; streamed through the operator workstation to the analysis server; no credentials transferred','objects':receipt},indent=2)+'\n')
 print(json.dumps({'file':name,'bytes':size,'verified':True}),flush=True)
