"""Bounded explanations of recorded comparisons; never infer a causal diagnosis."""
from __future__ import annotations
from collections import Counter

# Existing checks own windows, population gates and tolerances. Missing hops
# are deliberately not filled by subtracting values from unrelated populations.
BOUNDARIES=(
    ('generator-to-frontend','frontend',None),
    ('application-to-source','observer_to_source','counts_path'),
    ('source-to-storage','source_to_vm','counts_path'),
    ('storage-to-api','exact_request_count_api','counts_path'),
    ('application-errors-to-source','observer_errors_to_source','counts_path'),
    ('source-errors-to-storage','source_errors_to_vm','counts_path'),
    ('error-storage-to-api','exact_error_count_api','counts_path'),
    ('source-duration-to-storage','source_to_vm','duration'),
    ('duration-storage-to-api','exact_duration_api','counts_path'),
)


def summarize(report, verification):
    rows=[]
    for boundary,key,section in BOUNDARIES:
        check=verification.get(section,{}).get(key,{}) if section else verification.get(key,{})
        state=check.get('state','insufficient')
        if state not in ('pass','fail'):state='insufficient'
        rows.append({'boundary':boundary,'state':state,
                     'expected':check.get('reference'),'observed':check.get('observed'),
                     'absolute_error':check.get('absolute_error'),'bound':check.get('bound'),
                     'reason':check.get('reason'),
                     'evidence':'verification.json#/'+(section+'/' if section else '')+key,
                     'scope':'existing comparison population and time bounds; full partitions retained in evidence'})
    for row,(_,key,section) in zip(rows,BOUNDARIES):
        check=verification.get(section,{}).get(key,{}) if section else verification.get(key,{})
        members=check.get('checks',[])
        if members:
            row.update(comparison_count=len(members),
                       comparison_states=dict(Counter(c.get('state','insufficient') for c in members)),
                       examples=[_check_example(c) for c in members[:8]],
                       omitted_examples=max(0,len(members)-8),
                       population='per-identity comparisons, not a request-loss denominator')
        if key=='frontend' and members:
            identities=[c.get('request_id') for c in members]
            if all(identities) and len(set(identities))==len(identities):
                row.update(expected=len(identities),observed=sum(c.get('observer_status') is not None for c in members),
                           population='unique generator request identities joined to frontend records',
                           raw_frontend_records=check.get('observed'))
            else:row.update(expected=None,observed=None,population='missing or duplicate request identity; denominator unavailable')
    first=next((row for row in rows[:4] if row['state']=='fail'),None)
    if first is None:first=next((row for row in rows[4:] if row['state']=='fail'),None)
    missing=[row['boundary'] for row in rows if row['state']=='insufficient']
    manifest=report.get('manifest',{})
    return {'version':'boundary-summary-v1','workload_bounds':[manifest.get('start'),manifest.get('end')],
            'boundaries':rows,'first_demonstrated_discrepancy':first,
            'upstream_complete':bool(first is not None and all(row['state']=='pass' for row in rows[:rows.index(first)])),
            'missing_boundaries':missing,'cause':'exact cause unknown',
            'interpretation':('invalid required evidence; retained numerical discrepancies are diagnostic only'
                              if report.get('evidence',{}).get('valid') is False else
                              'recorded comparison boundaries; first discrepancy is not proof of its root cause'),
            'population_rule':'values from different intervals or populations are never subtracted to infer losses',
            'source_counter_brackets':_brackets(verification.get('source',{})),
            'storage_counter_brackets':_brackets(verification.get('vm',{})),
            'resource_comparisons':_resources(report.get('resources') or verification.get('resources',{})),
            'diagnostic_evidence':['verification.json','reference-status.json','structured/status.json',
                                   'transition.json','execution-diagnostics/']}


def _check_example(check):
    return {k:check[k] for k in ('state','reference','observed','absolute_error','bound','reason','labels',
                                 'timestamp','request_id','route','generator_status','observer_status') if k in check}


def _brackets(source):
    series=source.get('series',[])
    keys=('metric','labels','first','last','increase','resets','boundary_slack_seconds')
    return {'series_count':len(series),'examples':[{k:r[k] for k in keys if k in r} for r in series[:8]],
            'omitted_examples':max(0,len(series)-8),
            'scope':'actual per-series source/storage bracketing times; no common-window identity inferred'}


def _resources(resource):
    groups={}
    for row in resource.get('checks',[]):
        name=row.get('metric')
        if not isinstance(name,str):continue
        group=groups.setdefault(name,{'metric':name,'comparison_count':0,'eligible_aligned_measurements':0,
                                     'states':{},'examples':[]})
        group['comparison_count']+=1
        state=row.get('state','insufficient');group['states'][state]=group['states'].get(state,0)+1
        agreement=row.get('independent_agreement',{})
        if agreement.get('state') in ('pass','fail'):group['eligible_aligned_measurements']+=1
        if len(group['examples'])<8:
            group['examples'].append({k:row[k] for k in ('container','pod_uid','node_uid','collector_uid','state',
                'measurement_timestamp','raw_sample_timestamp_ns','stored_timestamp','evaluation_timestamp',
                'input_requested_at','input_received_at','cache_age_seconds','stored_sample_age_seconds',
                'conversion','preservation','independent_agreement','reason') if k in row})
    for group in groups.values():
        group['omitted_examples']=group['comparison_count']-len(group['examples'])
        group['population']='selected resource comparisons; not expected emitted samples or metric-loss percentage'
    return list(groups.values())


def render(summary, *, title='Boundary evidence summary'):
    """Small standalone artifact; raw evidence remains in its sealed bundle."""
    import html
    def escaped(value):return html.escape(str(value)) if value is not None else 'unavailable'
    columns=('boundary','state','expected','observed','comparison_count','reason','evidence')
    import json
    details=html.escape(json.dumps({k:summary.get(k) for k in ('boundaries','source_counter_brackets','storage_counter_brackets','resource_comparisons')},indent=2,sort_keys=True))
    rows=''.join('<tr>'+''.join('<td>'+escaped(row.get(key))+'</td>' for key in columns)+'</tr>'
                 for row in summary.get('boundaries',[]))
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>'+escaped(title)+'</title>'
            '<style>body{font:16px system-ui;margin:2rem;line-height:1.5}table{border-collapse:collapse;width:100%}'
            'th,td{border:1px solid #ccc;padding:.5rem;text-align:left;vertical-align:top}</style>'
            '<h1>'+escaped(title)+'</h1><p>'+escaped(summary.get('interpretation'))+'</p>'
            '<p>Workload bounds: '+escaped(summary.get('workload_bounds'))+'</p>'
            '<p>Cause: '+escaped(summary.get('cause'))+'</p>'
            '<p>'+escaped(summary.get('population_rule'))+'</p><table><thead><tr>'
            +''.join('<th>'+escaped(key)+'</th>' for key in columns)+'</tr></thead><tbody>'+rows+'</tbody></table><h2>Bounded population and timing examples</h2><pre>'+details+'</pre></html>')
