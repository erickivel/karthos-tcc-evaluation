"""Observed boundary traffic only; unknown traffic is never subtracted."""
import json
from pathlib import Path
from .models import read
from ..scoring import stamp


def classify(events, declarations, run_id):
    routes={(row.get('pod'),row.get('path')) for row in declarations if row.get('pod') and row.get('path')}
    counts=dict(observed=0,journey_requests=0,identified_probe_requests=0,unclassified_requests=0)
    for row in events:
        counts['observed']+=1
        if run_id and row.get('run_id')==run_id and row.get('request_id'):
            counts['journey_requests']+=1
        elif (row.get('pod'),row.get('uri')) in routes and str(row.get('user_agent','')).startswith('kube-probe/'):
            counts['identified_probe_requests']+=1
        else:
            counts['unclassified_requests']+=1
    return dict(state='observed' if counts['observed'] else 'insufficient',
                counts=counts if counts['observed'] else None, whole_cluster_coverage=False,
                scope='requests recorded by evaluation observers; internal calls without journey identity remain unclassified',
                limitations=['probes bypassing observers are not measured', 'user-agent plus declared route is attribution evidence, not authenticated caller identity',
                             'missing records do not establish zero traffic; no counts inferred from probe intervals'],
                excluded_from_telemetry=0)


def from_window(path, namespace, run_id, start, end):
    path=Path(path)
    try:
        pods=read(path/'k8s-pods.json')['items'];declarations=[]
        for pod in pods:
            if pod['metadata'].get('namespace')!=namespace:continue
            for container in pod.get('spec',{}).get('containers',[]):
                for name in ('livenessProbe','readinessProbe','startupProbe'):
                    http=container.get(name,{}).get('httpGet')
                    if http:declarations.append(dict(pod=pod['metadata']['name'],path=http.get('path'),port=http.get('port')))
        lo,hi=stamp(start),stamp(end)
        files=sorted((path/'logs').glob(namespace+'__*__eval-observer.log'))
        errors=[]
        def events():
            for file in files:
                pod=file.name.split('__')[1]
                with file.open() as handle:
                    for number,line in enumerate(handle,1):
                        offset=line.find('{')
                        if offset<0:continue
                        try:
                            row=json.loads(line[offset:])
                            if lo<=stamp(row['observed_at'])<hi:yield dict(row,pod=pod)
                        except (ValueError,KeyError,TypeError):errors.append(dict(file=file.name,line=number))
        result=classify(events(),declarations,run_id)
        result.update(declared_probe_routes=declarations,files=[str(p.relative_to(path)) for p in files],parse_errors=errors,
                      interval=dict(start=start,end=end,semantics='completion time [start,end)'))
        if errors:result['state']='insufficient'
        return result
    except (OSError,ValueError,KeyError,TypeError) as exc:
        return dict(state='insufficient',reason=str(exc),counts=None,whole_cluster_coverage=False)
