"""Immutable secondary research export. No infrastructure mutation or raw quarantine access."""
from __future__ import annotations
import base64,hashlib,json,re,threading,time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from .models import identifier

API='https://storage.googleapis.com/storage/v1/b/'
BUCKET=re.compile(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]')
MAX_OBJECTS=10000

class GCS:
    def __init__(self,ctx,expires):
        self.ctx=ctx;self.expires=expires;self.token=None;self.token_at=0;self.lock=threading.Lock()
    def remaining(self):
        left=self.expires-time.time()
        if left<=0:raise TimeoutError('secondary export budget exhausted; cleanup reserve retained')
        return left
    def request(self,method,url,params=None,data=None):
        with self.lock:
            if self.token is None or time.monotonic()-self.token_at>3000:
                value=self.ctx.command(['gcloud','auth','print-access-token'],timeout=min(30,self.remaining()),cleanup=True).stdout
                self.token=value.strip();self.token_at=time.monotonic()
        self.remaining()
        request=Request(url+('?' + urlencode(params) if params else ''),data=data,method=method,
                        headers={'Authorization':'Bearer '+self.token,'Accept-Encoding':'gzip',
                                 'Content-Type':'application/json' if data is None else 'application/octet-stream'})
        return urlopen(request,timeout=min(120,self.remaining()))
    def json(self,method,url,params=None):
        with self.request(method,url,params) as response:
            raw=response.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('secondary metadata byte bound exceeded')
        return json.loads(raw)
    def object_url(self,bucket,name):
        return API+quote(bucket,safe='')+'/o/'+quote(name,safe='')
    def list(self,bucket,prefix):
        rows=[];params={'prefix':prefix,'maxResults':1000};seen=set()
        while True:
            value=self.json('GET',API+bucket+'/o',params);rows.extend(value.get('items',[]))
            if len(rows)>MAX_OBJECTS:raise ValueError('secondary object count bound exceeded')
            token=value.get('nextPageToken')
            if not token:return rows
            if token in seen:raise ValueError('secondary listing repeated page')
            seen.add(token);params['pageToken']=token
    def rewrite(self,source,bucket,destination):
        endpoint=self.object_url(bucket,source['name'])+'/rewriteTo/b/'+quote(destination,safe='')+'/o/'+quote(source['name'],safe='')
        params={'sourceGeneration':source['generation'],'ifGenerationMatch':0}
        for _ in range(100):
            if self.remaining()<30:raise TimeoutError('insufficient secondary rewrite budget')
            try:value=self.json('POST',endpoint,params)
            except HTTPError as exc:
                if exc.code!=412:raise
                return self.json('GET',self.object_url(destination,source['name']))
            if value.get('done'):return value['resource']
            if not value.get('rewriteToken'):raise ValueError('missing secondary rewrite token')
            params['rewriteToken']=value['rewriteToken']
        raise ValueError('secondary rewrite continuation bound exceeded')
    def verify(self,bucket,row):
        digest=hashlib.md5();sha=hashlib.sha256();size=0
        with self.request('GET',self.object_url(bucket,row['name']),{'alt':'media','generation':row['generation']}) as response:
            while True:
                self.remaining();chunk=response.read1(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>int(row['size']):raise ValueError('secondary readback exceeds expected size')
                digest.update(chunk);sha.update(chunk)
        if size!=int(row['size']) or base64.b64encode(digest.digest()).decode()!=row['md5Hash']:
            raise ValueError('secondary readback checksum mismatch')
        return sha.hexdigest()
    def put_receipt(self,bucket,name,data):
        params={'uploadType':'media','name':name,'ifGenerationMatch':0}
        try:
            with self.request('POST','https://storage.googleapis.com/upload/storage/v1/b/'+bucket+'/o',params,data) as response:
                metadata=json.load(response)
        except HTTPError as exc:
            if exc.code!=412:raise
            metadata=self.json('GET',self.object_url(bucket,name))
        digest=self.verify(bucket,metadata)
        if digest!=hashlib.sha256(data).hexdigest():raise ValueError('secondary receipt conflict')
        return {'generation':metadata['generation'],'sha256':digest,'verified':True}

def backup_run(ctx,primary_receipt,*,store=None):
    """Snapshot completed research/control exports; errors never suppress cleanup.

    Later watchdog records are outside this snapshot and are not claimed backed up.
    Quarantine originals and Terraform state never enter normal research replication.
    """
    result={'version':1,'verified':False,'objects':[],'errors':[],
            'run_id':ctx.run_id,'started_at':time.time(),
            'scope':'run evidence and control objects visible at listing; excludes future cleanup records and quarantine'}
    try:
        run=identifier(ctx.run_id);foundation=ctx.state['terraform']['foundation']
        source=foundation['evidence_bucket'];target=foundation['backup_bucket']
        if not BUCKET.fullmatch(source) or not BUCKET.fullmatch(target) or source==target:raise ValueError('distinct valid primary and backup buckets required')
        prefix='gke-qualification-v1/runs/'+run+'/'
        if primary_receipt.get('verified') is not True or primary_receipt.get('uri')!='gs://'+source+'/'+prefix+'final-summary/':
            raise ValueError('verified final summary for this run required')
        expires=min(ctx.deadline-ctx.config['limits']['cleanup_reserve_seconds'],time.time()+1800)
        if expires-time.time()<30:raise TimeoutError('insufficient secondary export budget; cleanup reserve retained')
        store=store or GCS(ctx,expires);rows=[];names=set()
        for scope in (prefix,'control/'+run+'/'):
            for row in store.list(source,scope):
                name=row.get('name','')
                if not name.startswith(scope) or name in names:raise ValueError('secondary object scope or duplicate identity invalid')
                names.add(name)
                if name.startswith(prefix+'exports/secondary/'):continue
                if not str(row.get('generation','')).isdigit() or not row.get('md5Hash') or not str(row.get('size','')).isdigit():
                    raise ValueError('secondary source integrity metadata unavailable')
                rows.append(row)
                if len(rows)>MAX_OBJECTS:raise ValueError('secondary object count bound exceeded')
        if not rows:raise ValueError('secondary source inventory is empty')
        result['inventory_completed_at']=time.time()
        def transfer(row):
            item={'name':row['name'],'source_generation':row['generation'],'bytes':int(row['size'])}
            try:
                if time.time()>=expires:raise TimeoutError('secondary export budget exhausted')
                copied=store.rewrite(row,source,target)
                if copied.get('name')!=row['name'] or not str(copied.get('generation','')).isdigit():
                    raise ValueError('secondary destination identity invalid')
                if any(copied.get(key)!=row.get(key) for key in ('size','md5Hash','crc32c')):
                    raise ValueError('secondary destination differs from frozen source')
                item.update(backup_generation=copied['generation'],sha256=store.verify(target,copied),verified=True)
            except Exception as exc:item.update(verified=False,error=type(exc).__name__+': '+str(exc)[:1000])
            return item
        with ThreadPoolExecutor(max_workers=2) as pool:
            result['objects']=list(pool.map(transfer,rows))
        result['errors']=[{'name':item['name'],'error':item['error']} for item in result['objects'] if not item['verified']]
        result['verified']=not result['errors']
        result.update(primary_bucket=source,backup_bucket=target,finished_at=time.time())
        payload=json.dumps(result,sort_keys=True).encode()
        name=prefix+'exports/secondary/'+hashlib.sha256(payload).hexdigest()+'.json'
        result['receipts']={}
        for bucket in (source,target):
            result['receipts'][bucket]=store.put_receipt(bucket,name,payload)
        result['receipt_object']=name
    except Exception as exc:
        result['verified']=False;result['errors'].append({'error':type(exc).__name__+': '+str(exc)[:1000]})
    result['finished_at']=time.time()
    ctx.write(ctx.result/'secondary-export.json',result)
    return result
