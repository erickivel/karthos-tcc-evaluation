"""Offline replay in isolated, disposable VM; no published port or cloud resource."""
import subprocess,json,time,pathlib,hashlib
P=pathlib.Path(__import__('os').environ.get('KARTHOS_ANALYSIS_BASE','/srv/karthos-analysis')+'/exploratory')
VM='victoriametrics/victoria-metrics@sha256:b9b667f6709f20044328ddefae8869c56ced840ebafdf89bc3bba1f28f2d7e60'
HELPER='sha256:4a2340e53506ec86c94ab7f5bd4901aa2ddcdf743fd0c58e833c2fd9bc109fb6'
code='''import json,urllib.request,urllib.parse,pathlib,time
p=pathlib.Path('/inputs')
for i in range(30):
 try: urllib.request.urlopen('http://127.0.0.1:8428/health');break
 except Exception:time.sleep(.2)
data=p.joinpath('vm-raw.ndjson').read_bytes()
urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8428/api/v1/import',data=data)).read()
urllib.request.urlopen('http://127.0.0.1:8428/internal/force_flush').read()
queries=json.loads(p.joinpath('vm-parity/manifest.json').read_text())['queries'];out=[]
for q in queries:
 if q['api_file'] not in ['api-error-count.json','api-request-count.json']:continue
 params={'query':q['expression'],'time':q['to'],'nocache':1}
 r=json.load(urllib.request.urlopen('http://127.0.0.1:8428/api/v1/query?'+urllib.parse.urlencode(params)))
 selected=[x for x in r['data']['result'] if x['metric'].get('edge_client')=='frontend' and x['metric'].get('edge_server') in ['checkout','checkoutservice']]
 out.append({'query':q,'response':r,'checkout_total':sum(float(x['value'][1]) for x in selected)})
print(json.dumps(out))'''
results=[]
for label in ['B1','B2','B3','B4','B5','B6']:
 name='karthos-exploratory-replay-'+label.lower()
 subprocess.run(['docker','run','-d','--name',name,'--network','none','--memory','1g','--cpus','1','--tmpfs','/storage:rw,size=512m',VM,'-storageDataPath=/storage','-retentionPeriod=3'],check=True,stdout=subprocess.DEVNULL)
 try:
  r=subprocess.run(['docker','run','--rm','--network','container:'+name,'--memory','1g','--cpus','1','-v',str(P/'selected'/label)+':/inputs:ro','--entrypoint','python3',HELPER,'-c',code],check=True,capture_output=True,text=True)
  results.append({'baseline':label,'vm_image':VM,'input_sha256':hashlib.sha256((P/'selected'/label/'vm-raw.ndjson').read_bytes()).hexdigest(),'queries':json.loads(r.stdout)})
 finally:subprocess.run(['docker','rm','-f',name],check=True,stdout=subprocess.DEVNULL)
 print(label,[(x['query']['api_file'],x['checkout_total']) for x in results[-1]['queries']],flush=True)
(P/'outputs/replay.json').write_text(json.dumps({'results':results,'cleanup':'all six owned containers removed after use'},indent=2)+'\n')
