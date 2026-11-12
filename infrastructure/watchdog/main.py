"""Independent Cloud Run deadline enforcement. No credentials or SDK needed offline.

Runtime dependency: google-auth[requests] (pinned by controller Dockerfile).
Expose handler with functions-framework --target=handler --source=... . Cloud Run
IAM authenticates Scheduler OIDC before requests reach this handler; never grant
allUsers invoker. Inventory is read from a pinned GCS generation, not HTTP input.
"""
import hashlib
import uuid
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote

MAX_SECONDS = 12 * 3600


def _error(name, exc):
    result={'name':name,'type':type(exc).__name__}
    response=getattr(exc,'response',None)
    if response is not None:
        status=getattr(response,'status_code',None)
        if isinstance(status,int):result['http_status']=status
        try:
            payload=response.json();error=payload.get('error',{}) if isinstance(payload,dict) else {}
            message=error.get('message') if isinstance(error,dict) else None
            if isinstance(message,str):result['message']=message[:1500]
        except (ValueError,TypeError,AttributeError):pass
    return result


def effective_deadline(record, cloud, inventory_generation, now, *, inventory_object=None):
    short = record['deadline']
    handoff = record.get('readiness_handoff')
    if not handoff:
        return short
    try:
        long = handoff['study_deadline']
        latest = handoff['commit_before']
        started = record['started_at']
        if not started < latest < short <= long <= started + MAX_SECONDS:
            return short
        expected = {'run_id': record['run_id'], 'lease_token': record['lease']['token'],
                    'inventory_generation': handoff.get('inventory_generation', inventory_generation),
                    'inventory_object': handoff.get('inventory_object', inventory_object),
                    'study_deadline': long}
        prefix = 'control/' + record['run_id'] + '/inventory/'
        if not (re.fullmatch(r'[0-9]+', str(expected['inventory_generation']))
                and re.fullmatch(re.escape(prefix) + r'[a-f0-9]{64}\.json', str(expected['inventory_object']))):
            return short
        observation = cloud.read_handoff(record['run_id'])
        if observation is not None:
            created, actual = observation
            if started <= created < latest and created <= now and actual == expected:
                return long
    except Exception:
        # Storage unavailability or malformed handoff metadata cannot extend cleanup.
        pass
    return short


def discover_disks(record, cloud, resources, *, dry_run):
    """Bind every newly observed owned disk before it can become deletable."""
    if not cloud.verify_lease(record.get('lease')):
        raise ValueError('disk discovery project lease mismatch')
    candidates=cloud.list_owned_disks(record)
    previous={resource['name']:resource for resource in resources}
    pending=[]
    for candidate in candidates:
        existing=previous.get(candidate['name'])
        if existing is not None:
            if existing['kind']!='disk' or str(existing['uid'])!=str(candidate['uid']):
                raise ValueError('listed disk replaced known UID')
            continue
        intent_id=hashlib.sha256(json.dumps([record['project_id'],record['owner_id'],record['run_id'],candidate['name']],
                                          separators=(',',':')).encode()).hexdigest()[:32]
        intent={'kind':'disk','name':candidate['name'],'intent_id':intent_id,'observed_uid':str(candidate['uid']),
                'binding_object':'control/'+record['run_id']+'/uid-bindings/'+intent_id+'.json'}
        bound=cloud.reconcile_intent(intent,record,dry_run=dry_run)
        if bound is None:
            pending.append(candidate['name'])
            continue
        if bound['name']!=candidate['name'] or str(bound['uid'])!=str(candidate['uid']):
            raise ValueError('disk UID changed during immutable observation')
        previous[bound['name']]=bound
    return list(previous.values()),pending


def enforce(record, cloud, *, now, dry_run=True, inventory_generation=None, inventory_object=None):
    project = record['project_id']
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,28}[a-z0-9]', project):
        raise ValueError('invalid dedicated project')
    started, deadline = record['started_at'], record['deadline']
    if not 0 < deadline - started <= MAX_SECONDS:
        raise ValueError('deadline must be within 12 hours of start')
    if not record['resources'] and not record.get('creation_intents'):
        raise ValueError('nonempty immutable inventory or creation intent required')
    segments = {'cluster':('(?:zones|locations)/[a-z0-9-]+','clusters'),
                'instance':('zones/[a-z0-9-]+','instances'),
                'disk':('(?:zones|regions)/[a-z0-9-]+','disks'),
                'router':('regions/[a-z0-9-]+','routers'),
                'firewall':('global','firewalls'),
                'subnetwork':('regions/[a-z0-9-]+','subnetworks'),
                'network':('global','networks')}
    for resource in record['resources']+record.get('creation_intents',[]):
        scope, segment = segments.get(resource['kind'], ('!invalid','!invalid'))
        if not re.fullmatch(r'projects/' + re.escape(project) + '/' + scope + '/' + segment + r'/[a-z0-9-]+', resource['name']):
            raise ValueError('unsupported or cross-project resource')
        if (resource in record['resources'] and not resource.get('uid')) or not record.get('owner_id') or not record.get('run_id'):
            raise ValueError('ownership and UID inventory required')
    for intent in record.get('creation_intents',[]):
        if not re.fullmatch('[a-f0-9]{32}',str(intent.get('intent_id',''))) or intent.get('binding_object')!='control/'+record['run_id']+'/uid-bindings/'+intent['intent_id']+'.json':
            raise ValueError('unsafe creation intent binding')
    result = {'eligible': [], 'cleanup_actions_requested': [], 'absent': [], 'refused': [], 'errors': [], 'deferred': []}
    result['unresolved_creation_intents']=[intent['name'] for intent in record.get('creation_intents',[])]
    result['residual'] = [resource['name'] for resource in record['resources']]+result['unresolved_creation_intents']
    result['inventory_resource_count']=len(record['resources'])
    result['not_hard_guarantee'] = True
    deadline = effective_deadline(record, cloud, inventory_generation, now, inventory_object=inventory_object)
    result['effective_deadline'] = deadline
    requested=record.get('cleanup_requested_at')
    completion=False
    if now < deadline and record.get('completed_export_verified') is True and isinstance(requested,(int,float)) and started <= requested <= deadline and requested <= now:
        verifier=getattr(cloud,'verify_export',None)
        completion=bool(verifier and verifier(record.get('export_receipt',{})))
    result['completion_cleanup']=completion
    result['waiting_for_deadline'] = now < deadline and not completion
    if result['waiting_for_deadline']:
        # Arming performs an authenticated dry-run before any workload starts.
        # Validate the newly required read permission now, without UID bindings.
        if dry_run:
            try:
                if not cloud.verify_lease(record.get('lease')):
                    raise ValueError('disk discovery project lease mismatch')
                cloud.list_owned_disks(record)
                result['disk_discovery_verified']=True
            except Exception as exc:
                result['disk_discovery_verified']=False
                result['errors'].append(_error('owned-disk-discovery',exc))
                result['residual'].append('owned-disk-discovery-unverified')
        return result
    try:
        observed,pending_disks=discover_disks(record,cloud,list(record['resources']),dry_run=dry_run)
    except Exception as exc:
        result['errors'].append(_error('owned-disk-discovery',exc))
        result['residual'].append('owned-disk-discovery-unverified')
        return result
    result['unresolved_creation_intents'].extend(pending_disks)
    ranks={'cluster':0,'instance':0,'disk':0,'router':1,'firewall':2,'subnetwork':3,'network':4}
    resources=observed;unresolved=[];unobservable_ranks=[]
    for intent in record.get('creation_intents',[]):
        try:
            reconcile=getattr(cloud,'reconcile_intent',None)
            if not reconcile:raise ValueError('creation intent observation unavailable')
            bound=reconcile(intent,record,dry_run=dry_run)
            if bound is None:unresolved.append(intent)
            else:resources.append(bound)
        except Exception as exc:
            unresolved.append(intent);unobservable_ranks.append(ranks[intent['kind']]);result['errors'].append(_error(intent['name'],exc))
    result['unresolved_creation_intents']=[intent['name'] for intent in unresolved]+pending_disks
    result['inventory_resource_count']=len(resources)
    # A successfully observed-absent intent remains unresolved for late arrival,
    # but must not strand independently owned NAT/network resources. Present
    # compute and failed observations still gate dependent network operations.
    blocked_rank=min(unobservable_ranks,default=None)
    def process(resource):
        nonlocal blocked_rank
        rank=ranks[resource['kind']]
        if blocked_rank is not None and rank>blocked_rank:
            result['deferred'].append(resource['name']);return
        try:
            actual = cloud.get(resource)
            if actual is None:
                result['absent'].append(resource['name'])
            elif any(str(actual.get(key)) != str(expected) for key, expected in
                     [('uid', resource['uid']), ('owner_id', record['owner_id']), ('run_id', record['run_id'])]):
                result['refused'].append(resource['name']);blocked_rank=rank if blocked_rank is None else min(blocked_rank,rank)
            else:
                result['eligible'].append(resource['name']);blocked_rank=rank if blocked_rank is None else min(blocked_rank,rank)
                if not dry_run:
                    cloud.delete(dict(resource, owner_id=record['owner_id'], run_id=record['run_id']))
                    result['cleanup_actions_requested'].append(resource['name'])
        except Exception as exc:
            blocked_rank=rank if blocked_rank is None else min(blocked_rank,rank)
            result['errors'].append(_error(resource['name'],exc))
    # Cluster/instance deletion may complete late PVC creation. Re-list before
    # disks and before any dependent network cleanup, including all-absent runs.
    for resource in resources:
        if resource['kind'] in {'cluster','instance'}:process(resource)
    try:
        resources,pending_disks=discover_disks(record,cloud,resources,dry_run=dry_run)
        result['unresolved_creation_intents']=list(dict.fromkeys(result['unresolved_creation_intents']+pending_disks))
        result['inventory_resource_count']=len(resources)
    except Exception as exc:
        result['errors'].append(_error('post-compute-disk-discovery',exc))
        result['residual']=list(dict.fromkeys(result['residual']+['owned-disk-discovery-unverified']))
        return result
    for resource in sorted(resources,key=lambda r:ranks[r['kind']]):
        if resource['kind'] not in {'cluster','instance'}:process(resource)
    result['residual'] = list(dict.fromkeys(result['eligible'] + result['refused'] + result['deferred'] + result['unresolved_creation_intents'] + [error['name'] for error in result['errors']]))
    return result


class GCPCloud:
    def __init__(self, session, expected_run=None):
        self.session = session
        self.expected_run = expected_run

    def url(self, resource):
        base = 'https://container.googleapis.com/v1/' if resource['kind'] == 'cluster' else 'https://compute.googleapis.com/compute/v1/'
        return base + resource['name']

    def get(self, resource):
        response = self.session.get(self.url(resource), timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        value = response.json()
        labels = value.get('resourceLabels', value.get('labels', {}))
        if resource['kind'] in {'router','network','subnetwork','firewall'}:
            try:
                labels = json.loads(value.get('description', '{}'))
            except (ValueError, TypeError):
                labels = {}
        return {'uid': str(value.get('id', '')), 'owner_id': labels.get('evaluation-owner'), 'run_id': labels.get('evaluation-run'), 'nats':value.get('nats', []), 'users':value.get('users', [])}

    def list_owned_disks(self, record):
        """Exact owner/run filters, bounded zonal/regional pagination (REST v1)."""
        if not self.verify_lease(record.get('lease')):raise ValueError('disk list project lease mismatch')
        self.discovery_lease=record.get('lease')
        project=record['project_id']
        if not all(re.fullmatch('[a-z][a-z0-9-]{0,62}',record[key]) for key in ('owner_id','run_id')):
            raise ValueError('unsafe disk discovery label scope')
        scopes=set()
        for resource in record['resources']+record.get('creation_intents',[]):
            parts=resource['name'].split('/')
            if parts[2] in {'zones','regions'}:scopes.add((parts[2],parts[3]))
            elif parts[2]=='locations':
                scope='zones' if re.fullmatch(r'.+-[a-z]',parts[3]) else 'regions'
                scopes.add((scope,parts[3]))
        if not scopes:raise ValueError('no trusted disk location in inventory')
        found=[];deadline=time.monotonic()+120;pages=0
        for scope,location in sorted(scopes):
            token=None;seen=set()
            for _ in range(100):
                pages+=1
                if pages>100 or time.monotonic()>=deadline:raise ValueError('disk discovery pagination/time bound exceeded')
                params={'filter':'(labels.evaluation-owner = "'+record['owner_id']+'") AND (labels.evaluation-run = "'+record['run_id']+'")','maxResults':500}
                if token:params['pageToken']=token
                url='https://compute.googleapis.com/compute/v1/projects/'+project+'/'+scope+'/'+location+'/disks'
                response=self.session.get(url,params=params,timeout=min(30,max(.001,deadline-time.monotonic())))
                response.raise_for_status();page=response.json()
                rows=page.get('items',[])
                if not isinstance(rows,list) or len(rows)>500:raise ValueError('invalid owned disk list page')
                prefix='https://www.googleapis.com/compute/v1/projects/'+project+'/'+scope+'/'+location+'/disks/'
                for row in rows:
                    labels=row.get('labels',{})
                    if labels.get('evaluation-owner')!=record['owner_id'] or labels.get('evaluation-run')!=record['run_id']:
                        raise ValueError('disk list returned different ownership')
                    link=row.get('selfLink','');uid=str(row.get('id',''))
                    if not link.startswith(prefix) or not re.fullmatch('[a-z0-9-]+',link[len(prefix):]) or not uid:
                        raise ValueError('disk list returned different project/location or missing UID')
                    found.append({'kind':'disk','name':link.split('/compute/v1/',1)[1],'uid':uid})
                token=page.get('nextPageToken')
                if not token:break
                if not isinstance(token,str) or token in seen:raise ValueError('repeated disk pagination token')
                seen.add(token)
            else:raise ValueError('disk discovery page bound exceeded')
        return found

    def _lease_info(self, lease):
        bucket=os.environ['STATE_BUCKET']
        if not lease or not re.fullmatch('[a-f0-9]{32}',str(lease.get('token',''))) or lease.get('bucket')!=bucket or lease.get('object')!='control/active-run.json':return None
        url='https://storage.googleapis.com/storage/v1/b/'+quote(bucket,safe='')+'/o/'+quote('control/active-run.json',safe='')
        response=self.session.get(url,timeout=30)
        if response.status_code==404:return None
        response.raise_for_status();generation=str(response.json().get('generation',''))
        if not generation.isdigit():return None
        response=self.session.get(url,params={'alt':'media','generation':generation},timeout=30)
        response.raise_for_status();value=response.json()
        if value.get('token')!=lease.get('token') or value.get('run_id')!=self.expected_run or value.get('project_id')!=os.environ['PROJECT_ID'] or value.get('owner_id')!='gke-qualification-v1' or value.get('phase') not in {'active','cleanup-requested'}:return None
        return url,generation

    def verify_lease(self, lease):
        return self._lease_info(lease) is not None

    def release_lease(self, lease):
        info=self._lease_info(lease)
        if info is None:raise ValueError('project lease belongs to a different run or is absent')
        url,generation=info
        response=self.session.delete(url,params={'ifGenerationMatch':generation},timeout=30)
        response.raise_for_status()
        return True

    def reconcile_intent(self,intent,record,*,dry_run=False):
        if not re.fullmatch('[a-f0-9]{32}',intent.get('intent_id','')):raise ValueError('invalid creation intent ID')
        name='control/'+record['run_id']+'/uid-bindings/'+intent['intent_id']+'.json'
        if intent.get('binding_object')!=name:raise ValueError('unsafe UID binding object')
        if not self.verify_lease(record.get('lease')):raise ValueError('creation intent project lease mismatch')
        bucket=os.environ['INVENTORY_BUCKET']
        url='https://storage.googleapis.com/storage/v1/b/'+quote(bucket,safe='')+'/o/'+quote(name,safe='')
        response=self.session.get(url,timeout=30)
        actual=self.get(intent)
        if actual is not None and intent.get('observed_uid') is not None and actual['uid']!=str(intent['observed_uid']):
            raise ValueError('disk UID changed since discovery; refuse binding')
        if response.status_code==404:
            if actual is None:return None
            if actual['owner_id']!=record['owner_id'] or actual['run_id']!=record['run_id'] or not actual['uid']:raise ValueError('creation target is not owned')
            if dry_run:return None # A dry-run never creates even an observation object.
            value={'kind':intent['kind'],'name':intent['name'],'uid':actual['uid'],'project_id':record['project_id'],'owner_id':record['owner_id'],'run_id':record['run_id'],'lease_token':record['lease']['token'],'intent_id':intent['intent_id']}
            created=self.session.post('https://storage.googleapis.com/upload/storage/v1/b/'+quote(bucket,safe='')+'/o',params={'uploadType':'media','name':name,'ifGenerationMatch':'0'},data=json.dumps(value).encode(),headers={'Content-Type':'application/json'},timeout=30)
            if created.status_code!=412:created.raise_for_status()
            response=self.session.get(url,timeout=30)
        response.raise_for_status();generation=str(response.json().get('generation',''))
        if not generation.isdigit():raise ValueError('UID binding generation missing')
        response=self.session.get(url,params={'alt':'media','generation':generation},timeout=30);response.raise_for_status();value=response.json()
        expected={'kind':intent['kind'],'name':intent['name'],'project_id':record['project_id'],'owner_id':record['owner_id'],'run_id':record['run_id'],'lease_token':record['lease']['token'],'intent_id':intent['intent_id']}
        if any(value.get(key)!=item for key,item in expected.items()) or not value.get('uid'):raise ValueError('immutable UID observation belongs to another intent')
        if actual is not None and (actual['uid']!=str(value['uid']) or actual['owner_id']!=record['owner_id'] or actual['run_id']!=record['run_id']):raise ValueError('first observed UID replaced; deletion refused')
        return {'kind':intent['kind'],'name':intent['name'],'uid':str(value['uid']),'binding_generation':generation}

    def read_handoff(self, run_id):
        bucket = os.environ['INVENTORY_BUCKET']
        key = 'control/' + run_id + '/readiness-handoff.json'
        url = 'https://storage.googleapis.com/storage/v1/b/' + quote(bucket, safe='') + '/o/' + quote(key, safe='')
        response = self.session.get(url, timeout=10)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        metadata = response.json()
        generation = str(metadata['generation'])
        if not generation.isdigit() or not 0 < int(metadata['size']) <= 4096:
            return None
        created = datetime.fromisoformat(metadata['timeCreated'].replace('Z', '+00:00')).timestamp()
        response = self.session.get(url, params={'alt':'media', 'generation':generation}, timeout=10)
        response.raise_for_status()
        return created, response.json()

    def verify_export(self, receipt):
        objects=receipt.get('objects',[]);bucket=os.environ['INVENTORY_BUCKET']
        prefix=receipt.get('uri','')
        if not re.fullmatch(r'gs://'+re.escape(bucket)+r'/gke-qualification-v1/runs/[a-z][a-z0-9-]{0,47}/final-summary/',prefix):return False
        if self.expected_run is not None and prefix!='gs://'+bucket+'/gke-qualification-v1/runs/'+self.expected_run+'/final-summary/':return False
        object_prefix=prefix.split(bucket+'/',1)[1];observed={}
        for item in objects:
            name=item.get('object','')
            if item.get('bucket')!=bucket or not name.startswith(object_prefix) or not str(item.get('generation','')).isdigit() or not re.fullmatch('[a-f0-9]{64}',item.get('sha256','')):
                return False
            relative=name[len(object_prefix):]
            if relative in observed or relative.startswith('/') or '..' in relative.split('/'):return False
            response=self.session.get('https://storage.googleapis.com/storage/v1/b/'+quote(bucket,safe='')+'/o/'+quote(name,safe=''),params={'alt':'media','generation':str(item['generation'])},timeout=30)
            response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest()!=item['sha256']:return False
            observed[relative]=(item['sha256'],response.content)
        if not {'GKE-SHA256SUMS','run-report.json'} <= set(observed):return False
        declared={}
        try:
            for line in observed['GKE-SHA256SUMS'][1].decode().splitlines():
                checksum,name=line.split('  ',1)
                if name in declared:return False
                declared[name]=checksum
        except (ValueError,UnicodeDecodeError):return False
        return set(declared)==set(observed)-{'GKE-SHA256SUMS'} and all(observed[name][0]==checksum for name,checksum in declared.items())

    def delete(self, resource):
        # APIs lack atomic UID deletion preconditions. Re-check immediately and
        # prohibit concurrent recreations until this watchdog is disarmed.
        current = self.get(resource)
        if current is None:
            return
        if any(str(current.get(key)) != str(resource[key]) for key in ('uid', 'owner_id', 'run_id')):
            raise ValueError('resource UID or ownership changed before deletion')
        if resource['kind']=='disk':
            if not self.verify_lease(getattr(self,'discovery_lease',None)):
                raise ValueError('disk delete project lease mismatch')
            if current.get('users'):
                raise ValueError('disk is attached; deletion deferred')
        # routers.delete also removes attached NAT gateways. Avoid a separate
        # patch requiring network-policy updates; ownership/UID is checked above.
        response = self.session.delete(self.url(resource), timeout=60)
        if response.status_code != 404:
            response.raise_for_status()


def handler(request):
    headers = {'Content-Type':'application/json'}
    if request.method != 'POST':
        return (json.dumps({'error':'method not allowed', 'not_hard_guarantee':True, 'residual':['inventory-not-checked']}), 405, headers)
    try:
        return _handle(request)
    except Exception as exc:
        # Do not expose credentials, inventory bodies or cloud response text.
        return (json.dumps({'error':type(exc).__name__, 'not_hard_guarantee':True, 'residual':['cleanup-not-verified']}), 500, headers)


def _handle(request):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
    session = AuthorizedSession(credentials)
    bucket = os.environ['INVENTORY_BUCKET']
    name = os.environ['INVENTORY_OBJECT']
    generation = os.environ['INVENTORY_GENERATION']
    if not generation.isdigit():
        raise ValueError('inventory generation must be pinned')
    url = 'https://storage.googleapis.com/storage/v1/b/' + quote(bucket, safe='') + '/o/' + quote(name, safe='')
    response = session.get(url, params={'alt':'media', 'generation':generation}, timeout=30)
    response.raise_for_status()
    record = response.json()
    if record['project_id'] != os.environ['PROJECT_ID']:
        raise ValueError('inventory project does not match deployment')
    cloud=GCPCloud(session,expected_run=record['run_id'])
    if not cloud.verify_lease(record.get('lease')):
        return (json.dumps({'error':'project lease mismatch','residual':['lease-ownership-unverified'],'not_hard_guarantee':True}),409,{'Content-Type':'application/json'})
    dry_run=str(getattr(request,'headers',{}).get('X-Karthos-Dry-Run','')).lower()=='true'
    result = enforce(record, cloud, now=time.time(), dry_run=dry_run, inventory_generation=generation, inventory_object=name)
    result.update(project_id=record['project_id'],owner_id=record['owner_id'],run_id=record['run_id'],inventory_generation=generation,cloud_run_definition_retained=True,cloud_run_min_instances=0,active_instances_verified=False)
    result['compute_network_absence_verified']=not result['residual'] and len(result['absent'])==result['inventory_resource_count']
    result['not_hard_guarantee'] = True
    result['cleanup_semantics'] = 'best-effort asynchronous cloud deletion; scheduler delay up to five minutes plus API completion time'
    # Every observation is create-only durable evidence; no GCS delete rights.
    def persist():
        key='control/'+record['run_id']+'/teardown/'+str(time.time_ns())+'-'+uuid.uuid4().hex+'.json'
        response=session.post('https://storage.googleapis.com/upload/storage/v1/b/'+quote(bucket,safe='')+'/o',params={'uploadType':'media','name':key,'ifGenerationMatch':'0'},data=json.dumps(result).encode(),headers={'Content-Type':'application/json'},timeout=30)
        response.raise_for_status()
    if not dry_run:persist()
    # Only observation of absence, including network layers, permits timer pause.
    if not result['residual'] and len(result['absent']) == result['inventory_resource_count'] and not dry_run:
        scheduler = os.environ['SCHEDULER_NAME']
        prefix = 'projects/' + record['project_id'] + '/locations/'
        if not scheduler.startswith(prefix) or '/jobs/' not in scheduler:
            raise ValueError('invalid scheduler identity')
        response = session.post('https://cloudscheduler.googleapis.com/v1/' + scheduler + ':pause', json={}, timeout=30)
        response.raise_for_status()
        result['scheduler_paused'] = True
        try:persist()
        except Exception:
            # Keep retries available if durable completion evidence failed.
            response=session.post('https://cloudscheduler.googleapis.com/v1/'+scheduler+':resume',json={},timeout=30)
            response.raise_for_status()
            raise
        result['lease_released']=cloud.release_lease(record.get('lease'))
        # If this write fails, the prior absence+timer-pause observation remains;
        # never resume another run's shared Scheduler after releasing ownership.
        persist()
    return (json.dumps(result), 200, {'Content-Type':'application/json'})
