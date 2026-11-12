"""Generic full-series API/storage parity over the validated product compiler."""
import json,math,urllib.parse
from pathlib import Path
from .models import write,read
from ..evidence_v4 import vm_time_identity
from ..series_identity import partition_key
from ..scoring import stamp


def identity(entity,edge,labels,at):
    return (entity or None,edge or None,partition_key(labels),vm_time_identity(at)['timestamp_ms'])


def compare(api,document,source,*,metric=None,unit=None):
    """Compare both directions, preserving every semantic status/dimension."""
    if document.get('error'):return {'state':'insufficient','reason':document['error'],'checks':[]}
    points={};api_points={};checks=[]
    for response in [document.get('response',{}),document.get('final',{}).get('response',{})]:
        if not response:continue
        if response.get('status')!='success':return {'state':'insufficient','reason':'VM compiled query unsuccessful','checks':[]}
        for row in response.get('data',{}).get('result',[]):
            labels=row.get('metric',{})
            for at,value in row.get('values',[row['value']] if 'value' in row else []):
                key=identity(labels.get('entity_id'),labels.get('edge_id'),labels,at)
                points.setdefault(key,set()).add(float(value))
    for sample in api.get('data',{}).get('samples',[]):
        labels=sample.get('labels',{})
        if metric and sample.get('metric_name')!=metric:checks.append({'state':'fail','reason':'wrong public metric name','sample':sample})
        if unit and sample.get('unit')!=unit:checks.append({'state':'fail','reason':'public unit differs from product compiler','sample':sample,'expected_unit':unit})
        edge=sample.get('edge')
        if edge and (edge.get('source_id')!=labels.get('edge_client_id') or edge.get('target_id')!=labels.get('edge_server_id')):checks.append({'state':'fail','reason':'public edge identity conflicts with preserved dimensions','sample':sample})
        if labels.get('source')!=source:checks.append({'state':'fail','reason':'wrong public source provenance','sample':sample})
        key=identity(sample.get('entity_id'),sample.get('edge_id'),labels,sample['timestamp'])
        api_points.setdefault(key,set()).add(float(sample['value']))
    for key in sorted(set(points)|set(api_points),key=str):
        a,b=points.get(key,set()),api_points.get(key,set());detail={'identity':str(key),'stored_values':sorted(a),'api_values':sorted(b)}
        if len(a)!=1 or len(b)!=1:checks.append(dict(detail,state='insufficient',reason='missing or ambiguous complete series/evaluation time'));continue
        value,observed=next(iter(a)),next(iter(b))
        if not math.isfinite(value) or not math.isfinite(observed):checks.append(dict(detail,state='insufficient',reason='nonfinite samples'));continue
        checks.append(dict(detail,state='pass' if abs(value-observed)<=1e-9 else 'fail',absolute_error=abs(value-observed)))
    state='fail' if any(c['state']=='fail' for c in checks) else 'pass' if checks and all(c['state']=='pass' for c in checks) else 'insufficient'
    return {'state':state,'checks':checks,'scope':'bidirectional same product-rendered query, source, full identity and fixed evaluation times; independent accuracy is separate'}


def capture(ctx,root,vm_base,metrics,assignments,start,end):
    """Caller holds explicit cluster target and VM forward; no custom query policy."""
    from .. import runner as r
    from ..semantics_pilot import vm_query_request
    from datetime import datetime,timezone
    def iso(value):return datetime.fromtimestamp(stamp(value),timezone.utc).isoformat()
    results=[]
    for name in metrics:
        source=assignments.get(name);params={'metric_name':name,'from':iso(start),'to':iso(end),'step':'15s','source':source}
        record={'metric':name,'source':source,'params':params,'compiler_revision':ctx.manifest['product_pins']['karthos_revision']}
        try:
            if not source:raise ValueError('deployed source assignment unavailable')
            helper=Path(ctx.root)/'evaluation/helpers/render_query.go'
            compiled=json.loads(r.cmd(['go','run',str(helper)],cwd=r.REPO,input_text=json.dumps(params),timeout=120).stdout)
            record['compiled']=compiled
            request=vm_query_request(compiled['expression'],start=compiled['from'],end=compiled['to'],step=str(compiled['step_seconds']))
            document={'request':request,'response':r.get_json(vm_base+'/api/v1/query_range?'+urllib.parse.urlencode(request))}
            points=compiled['evaluation_points']
            if len(points)>1 and abs(stamp(points[-1])-stamp(points[-2])-compiled['step_seconds'])>1e-6:
                extra=vm_query_request(compiled['expression'],time=points[-1]);document['final']={'request':extra,'response':r.get_json(vm_base+'/api/v1/query?'+urllib.parse.urlencode(extra))}
            write(Path(root)/'parity'/(name+'.json'),document)
            record['result']=compare(read(Path(root)/'api'/(name+'.json')),document,source,metric=name,unit=compiled.get('unit'))
        except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:record.update(error=str(exc),result={'state':'insufficient','reason':str(exc),'checks':[]})
        results.append(record)
    report={'schema_version':1,'metrics':results,'fixed_window':{'start':start,'end':end},'independent_accuracy_claimed':False}
    write(Path(root)/'parity/manifest.json',report);return report
