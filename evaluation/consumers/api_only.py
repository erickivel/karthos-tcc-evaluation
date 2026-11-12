"""Public HTTP API consumer. No Kubernetes, storage, cloud SDK or raw mounts.

python3 -m evaluation.consumers.api_only --endpoint URL --before RFC3339 \
    --after RFC3339 --query '{"metric_name":"cpu.usage","source":"kubelet"}'
"""
import argparse
from datetime import datetime
import json
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('API redirects are not allowed')


def check_schema(value,schema,components,path='$'):
    """Check the types, fields and constraints used by the pinned API responses."""
    if '$ref' in schema:
        prefix='#/components/schemas/'
        reference=schema['$ref']
        if not reference.startswith(prefix):
            raise ValueError('unsupported OpenAPI schema reference '+reference)
        return check_schema(value,components[reference[len(prefix):]],components,path)
    for part in schema.get('allOf',[]):
        check_schema(value,part,components,path)
    if 'oneOf' in schema:
        matches=0
        for part in schema['oneOf']:
            try:
                check_schema(value,part,components,path)
                matches+=1
            except ValueError:
                pass
        if matches!=1:
            raise ValueError(path+' does not satisfy exactly one OpenAPI schema')
    tests={'object':lambda: isinstance(value,dict),'array':lambda: isinstance(value,list),
           'string':lambda: isinstance(value,str),'integer':lambda: type(value) is int,
           'number':lambda: type(value) in (int,float),'boolean':lambda: type(value) is bool,
           'null':lambda: value is None}
    types=schema.get('type',[])
    types=[types] if isinstance(types,str) else types
    if types and not any(tests[k]() for k in types):
        raise ValueError(path+' has the wrong OpenAPI type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path+' has an unknown enum value')
    if isinstance(value,dict):
        missing=set(schema.get('required',()))-set(value)
        if missing:
            raise ValueError(path+' is missing '+', '.join(sorted(missing)))
        for key,item in value.items():
            fields=schema.get('properties',{})
            sub=fields.get(key,schema.get('additionalProperties',{}))
            if sub is False:
                raise ValueError(path+'.'+key+' is not in the contract')
            if isinstance(sub,dict) and sub:
                check_schema(item,sub,components,path+'.'+key)
    if isinstance(value,list) and 'items' in schema:
        for index,item in enumerate(value):
            check_schema(item,schema['items'],components,f'{path}[{index}]')
    if type(value) in (int,float) and 'minimum' in schema and value<schema['minimum']:
        raise ValueError(path+' is below the OpenAPI minimum')
    if isinstance(value,str) and schema.get('format')=='date-time':
        if 'T' not in value or datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None:
            raise ValueError(path+' lacks a date-time with timezone')


class APIConsumer:
    def __init__(self, endpoint, *, timeout=10, max_pages=100, page_size=1000, contract=None, timeout_cap=None):
        url=urlsplit(endpoint)
        if (url.scheme not in ('http','https') or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path.rstrip('/') not in ('','/api/v1')):
            raise ValueError('endpoint must be HTTP(S) origin or /api/v1 without credentials/query/fragment')
        self.endpoint=endpoint.rstrip('/')
        if not self.endpoint.endswith('/api/v1'): self.endpoint+='/api/v1'
        if timeout<=0 or not 1<=max_pages<=10000 or not 1<=page_size<=1000:
            raise ValueError('positive bounded timeout/page controls required')
        self.timeout=timeout; self.max_pages=max_pages; self.page_size=page_size
        self.opener=build_opener(_NoRedirect());self.contract=contract
        self.contract_errors=[];self.timeout_cap=timeout_cap

    def _get(self, route, params):
        start=time.time()
        request=Request(self.endpoint+route+'?'+urlencode(params,doseq=True),headers={'Accept':'application/json'})
        timeout=self.timeout if self.timeout_cap is None else min(self.timeout,self.timeout_cap())
        with self.opener.open(request,timeout=timeout) as response:
            raw=response.read(32*1024*1024+1)
        if len(raw)>32*1024*1024: raise ValueError('API response exceeds byte bound')
        payload=json.loads(raw)
        if self.contract:
            schema=self.contract['responses'][route]
            try:
                check_schema(payload,schema,self.contract['schemas'])
            except ValueError as exc:
                self.contract_errors.append({'route':route,'reason':str(exc)})
                raise
        return payload, {'requested_at':start,'received_at':time.time(),'route':route,'params':dict(params)}

    def _pages(self, route, key, params, *, snapshot=None):
        rows=[];requests=[];seen=set();selected=snapshot
        params=dict(params,page_size=self.page_size)
        for _ in range(self.max_pages):
            body,observation=self._get(route,params);requests.append(observation)
            data=body.get('data')
            if not isinstance(data,dict) or not isinstance(data.get(key),list):
                raise ValueError('invalid API page envelope')
            if route.startswith('/graph/') and 'from' not in params:
                current=data.get('snapshot')
                if not isinstance(current,dict) or current.get('id') is None or not current.get('created_at'):
                    raise ValueError('graph snapshot identity unavailable')
                if selected is not None and current!=selected: raise ValueError('graph snapshot changed during pagination')
                selected=current
            rows.extend(data[key])
            token=body.get('next_token')
            if not token:
                return {'state':'observed',key:rows,'snapshot':selected,'requests':requests,
                        'total':data.get('total'),'query':data.get('query')}
            if token in seen: raise ValueError('repeated API pagination token')
            seen.add(token);params['next_token']=token
        raise ValueError('API page limit exceeded')

    def graph(self, *, at):
        try:
            nodes=self._pages('/graph/nodes','nodes',{'at':at})
            # Pin the separate edges request to this exact snapshot's creation.
            edges=self._pages('/graph/edges','edges',{'at':nodes['snapshot']['created_at']},snapshot=nodes['snapshot'])
            snapshot=nodes['snapshot']
            requested=datetime.fromisoformat(at.replace('Z','+00:00'))
            observed=datetime.fromisoformat(snapshot['created_at'].replace('Z','+00:00'))
            return {'state':'observed','at':at,'snapshot':snapshot,
                    'snapshot_lag_seconds':(requested-observed).total_seconds(),
                    'nodes':nodes['nodes'],'edges':edges['edges'],'requests':nodes['requests']+edges['requests']}
        except (ValueError,OSError) as exc:
            return {'state':'insufficient','at':at,'reason':str(exc)}

    def edge_history(self, before, after, source_id=None, *, target_id=None):
        params={'from':before,'to':after,'relation':'calls'}
        if source_id:
            params['source_id']=source_id
        if target_id:
            params['target']=target_id
        try:
            payload,request=self._get('/graph/edges',params)
            rows=payload['data']['edges']
            if not isinstance(rows,list):
                raise ValueError('invalid edge observation envelope')
            return {'state':'observed','edges':rows,'requests':[request]}
        except (ValueError,OSError,KeyError,TypeError) as exc:
            return {'state':'insufficient','reason':str(exc)}

    def metrics(self, query):
        try:
            return self._pages('/metrics','samples',query)
        except (ValueError,OSError) as exc:
            return {'state':'insufficient','reason':str(exc),'query':query,'samples':[]}

    def observe(self, before, after, *, queries=()):
        first=self.graph(at=before);second=self.graph(at=after)
        changed=(first['snapshot']['id']!=second['snapshot']['id']
                 if first['state']==second['state']=='observed' else None)
        return {'before':first,'after':second,'snapshot_changed':changed,
                'metrics':[self.metrics(query) for query in queries],
                'history_claim':'Only requested retained snapshots and metric responses are observed; no interpolation.'}


def answer_questions(graph, metrics, *, service='checkout', namespace=None, service_id=None, edge_history=None):
    """Report explicit API facts; structural association is not inferred traffic."""
    insufficient=lambda reason: {'state':'insufficient','reason':reason}
    if graph.get('state')!='observed':
        return {key:insufficient('graph unavailable') for key in ('callers','replicas','node_placements','resource_traffic')}
    nodes=graph['nodes']; edges=graph['edges']; by_id={n['id']:n for n in nodes}
    targets={n['id'] for n in nodes if n.get('kind')=='service' and
             (n['id']==service_id if service_id else n.get('name')==service and
              (namespace is None or n.get('namespace')==namespace))}
    if len(targets)!=1:
        return {key:insufficient('service identity missing or ambiguous')
                for key in ('callers','replicas','node_placements','resource_traffic')}
    traffic=edges if edge_history is None else edge_history
    callers=[{'source':e['source'],'target':e['target']} for e in traffic
             if e.get('relation')=='calls' and e.get('target') in targets]
    workloads={e['target'] for e in edges if e.get('relation')=='exposes' and e.get('source') in targets
               and by_id.get(e.get('target'),{}).get('kind')=='workload'}
    descendants=set(workloads)
    while True:
        children={e['target'] for e in edges if e.get('relation')=='contains'
                  and e.get('source') in descendants and by_id.get(e.get('target'),{}).get('kind') in ('workload','pod')}
        if children <= descendants:
            break
        descendants.update(children)
    pods={id for id in descendants if by_id[id]['kind']=='pod'}
    placements=[{'pod':e['source'],'node':e['target']} for e in edges
                if e.get('relation')=='runs_on' and e.get('source') in pods
                and by_id.get(e.get('target'),{}).get('kind')=='node']
    samples=[sample for result in metrics if result.get('state')=='observed' for sample in result.get('samples',[])]
    return {'callers':{'state':'observed' if callers else 'insufficient','observed':callers,
                       'scope':'observed calls in the requested interval' if edge_history is not None else 'calls edges in selected snapshot'},
            'replicas':{'state':'observed' if workloads else 'insufficient','observed':len(pods) if workloads else None,
                        'scope':'visible pods linked via service/workload ownership; desired replica count unavailable'},
            'node_placements':{'state':'observed' if pods and len(placements)==len(pods)==len({item['pod'] for item in placements}) else 'insufficient','observed':placements},
            'resource_traffic':{'state':'observed' if samples else 'insufficient','observed':samples,
                               'scope':'caller-selected public metric queries; preserve timestamp, labels and value_label as returned'}}


def graph_calls_source(metric_source):
    return {'traces.servicegraph':'traces.otlp','mesh.istio':'mesh.istio'}.get(metric_source)


def scoped_service(graph, namespace, name):
    candidates=[row for row in graph.get('nodes',[]) if row.get('kind')=='service'
                and row.get('namespace')==namespace and row.get('name')==name]
    return candidates[0]['id'] if len(candidates)==1 and candidates[0].get('id') else None


def observation_intersects(edge, before, after):
    try:
        first,last=(datetime.fromisoformat(edge[key].replace('Z','+00:00')) for key in ('first_seen','last_seen'))
        start,end=(datetime.fromisoformat(value.replace('Z','+00:00')) for value in (before,after))
        return first<=last and first<=end and last>=start
    except (AttributeError,KeyError,TypeError,ValueError):
        return False


def thesis_answers(client,before,after,*,namespace,service,source,container,history_only=False):
    result=client.observe(before,after)
    graph=result['after']
    selected=answer_questions(graph,[],service=service,namespace=namespace)
    previous=answer_questions(result['before'],[],service=service,namespace=namespace)
    target_id=scoped_service(graph,namespace,service)
    caller_id=scoped_service(graph,namespace,'frontend')
    calls_source=graph_calls_source(source)
    interval=(client.edge_history(before,after,calls_source,target_id=target_id)
              if not history_only and graph['state']=='observed' and target_id and calls_source else
              {'state':'insufficient','reason':'scoped checkout identity or graph observation source unavailable'})
    answers={}
    for graph_key, facts in (('after',selected),('before',previous)):
        graph_nodes={row['id']:row for row in result[graph_key].get('nodes',[])}
        placements=facts['node_placements']
        replicas=[{'pod':graph_nodes[p['pod']]['name'],'node':graph_nodes[p['node']]['name'],
                   'health':graph_nodes[p['pod']].get('status','unknown')} for p in placements.get('observed') or []]
        answers[graph_key+'_replicas']={'state':placements['state'],'value':sorted(replicas,key=lambda p:p['pod'])}
    if graph['state']=='observed' and not history_only:
        names={row['id']:row for row in graph['nodes']}
        call_rows=[];unresolved=[]
        for edge in interval.get('edges',[]):
            if edge.get('target')!=target_id or edge.get('relation')!='calls':
                continue
            caller=names.get(edge.get('source'))
            if caller is None:
                unresolved.append(edge.get('source'))
            elif (caller.get('namespace')==namespace and caller.get('kind')=='service'
                  and edge.get('provenance')==[calls_source] and observation_intersects(edge,before,after)):
                call_rows.append({'caller':caller['name'],'caller_id':caller['id'],'target_id':target_id,
                                  'namespace':namespace,'provenance':edge['provenance'],
                                  'first_seen':edge['first_seen'],'last_seen':edge['last_seen']})
        answers['interval-callers']={'state':'observed' if interval['state']=='observed' and not unresolved else 'insufficient',
                                    'value':call_rows,'unresolved_caller_ids':unresolved}
        pods={row['pod'] for row in answers['after_replicas']['value']}
        candidates=sorted((row for row in graph['nodes'] if row.get('kind')=='container'
                    and row.get('namespace')==namespace and row.get('name')==container
                    and row.get('labels',{}).get('k8s.pod.name') in pods),key=lambda row:row['id'])
        if candidates:
            entity=candidates[0]
            samples=[client.metrics({'entity_id':entity['id'],'metric_name':metric,'from':before,'to':after,'step':'15s','source':'kubelet'})
                     for metric in ('container.cpu.usage','container.memory.usage')]
            answers['container-resource-history']={'state':'observed' if all(r.get('samples') for r in samples) else 'insufficient',
                                                  'container':[namespace,entity['labels']['k8s.pod.name'],entity['name']],
                                                  'value':samples}
        if target_id and caller_id and source:
            seconds=(datetime.fromisoformat(after.replace('Z','+00:00'))-
                     datetime.fromisoformat(before.replace('Z','+00:00'))).total_seconds()
            query={'edge_source_id':caller_id,'edge_target_id':target_id,'metric_name':'servicegraph.request.error.count',
                   'from':before,'to':after,'source':source}
            if 60<=seconds<=3600 and seconds.is_integer():
                query.update(aggregation='increase',window=f'{int(seconds)}s',**{'from':after})
            errors=client.metrics(query)
            answers['request-error-derivation']={'state':'observed' if errors.get('samples') else 'insufficient',
                                                 'value':errors}
    result['answers']=answers
    result['edge_history']=interval
    result['scope']={'namespace':namespace,'service':service,'target_id':target_id,
                     'declared_caller_id':caller_id,'metric_source':source,'calls_source':calls_source,
                     'from':before,'to':after}
    result['openapi_validated']=bool(client.contract) and not client.contract_errors
    result['openapi_errors']=client.contract_errors
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint',required=True);parser.add_argument('--before',required=True)
    parser.add_argument('--after',required=True);parser.add_argument('--query',action='append',default=[])
    parser.add_argument('--service',default='checkout')
    args=parser.parse_args(argv)
    result=APIConsumer(args.endpoint).observe(args.before,args.after,queries=[json.loads(q) for q in args.query])
    result['answers']=answer_questions(result['after'],result['metrics'],service=args.service)
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0 if result['before']['state']==result['after']['state']=='observed' else 2


if __name__=='__main__':
    raise SystemExit(main())
