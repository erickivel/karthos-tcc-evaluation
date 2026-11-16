"""Permission and bounded log-write smoke using attached controller identity."""
import json
import urllib.request


def request(url, *, token=None, payload=None, metadata=False):
    headers = {'Metadata-Flavor':'Google'} if metadata else {'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    req = urllib.request.Request(url,headers=headers,data=json.dumps(payload).encode() if payload else None)
    try:
        with urllib.request.urlopen(req,timeout=15) as response: body=response.read().decode()
    except Exception as exc:
        # Do not include request objects, headers, tokens or response bodies in failures.
        raise RuntimeError('controller logging permission preflight request failed: '+type(exc).__name__) from None
    return json.loads(body) if not metadata or url.endswith('/token') else body.strip()


def verify(project, *, request=request):
    base='http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/'
    principal=request(base+'email',metadata=True)
    token=request(base+'token',metadata=True)['access_token']
    required='logging.logEntries.create'
    result=request('https://cloudresourcemanager.googleapis.com/v1/projects/'+project+':testIamPermissions',
                   token=token,payload={'permissions':[required]})
    if required not in result.get('permissions',[]):
        raise ValueError('attached controller identity '+principal+' lacks '+required)
    import uuid
    insert_id='controller-preflight-'+uuid.uuid4().hex
    request('https://logging.googleapis.com/v2/entries:write',token=token,payload={
        'logName':'projects/'+project+'/logs/karthos-controller-preflight',
        'resource':{'type':'global','labels':{'project_id':project}},
        'entries':[{'insertId':insert_id,'severity':'INFO',
                    'textPayload':'Karthos controller logging preflight'}]})
    return {'principal':principal,'project':project,'permission':required,'verified':True,
            'logging_write_accepted':True,'insert_id':insert_id,
            'scope':'one bounded entry accepted by Logging API; not a readback'}


if __name__=='__main__':
    project=request('http://metadata.google.internal/computeMetadata/v1/project/project-id',metadata=True)
    print(json.dumps(verify(project)))
