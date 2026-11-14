"""Bounded explanations of recorded comparisons; never infer a causal diagnosis."""
from __future__ import annotations

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
            'diagnostic_evidence':['verification.json','reference-status.json','structured-capture/status.json',
                                   'transition.json','execution-diagnostics/']}


def render(summary, *, title='Boundary evidence summary'):
    """Small standalone artifact; raw evidence remains in its sealed bundle."""
    import html
    def escaped(value):return html.escape(str(value)) if value is not None else 'unavailable'
    columns=('boundary','state','expected','observed','absolute_error','bound','reason','evidence')
    rows=''.join('<tr>'+''.join('<td>'+escaped(row.get(key))+'</td>' for key in columns)+'</tr>'
                 for row in summary.get('boundaries',[]))
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>'+escaped(title)+'</title>'
            '<style>body{font:16px system-ui;margin:2rem;line-height:1.5}table{border-collapse:collapse;width:100%}'
            'th,td{border:1px solid #ccc;padding:.5rem;text-align:left;vertical-align:top}</style>'
            '<h1>'+escaped(title)+'</h1><p>'+escaped(summary.get('interpretation'))+'</p>'
            '<p>Workload bounds: '+escaped(summary.get('workload_bounds'))+'</p>'
            '<p>Cause: '+escaped(summary.get('cause'))+'</p>'
            '<p>'+escaped(summary.get('population_rule'))+'</p><table><thead><tr>'
            +''.join('<th>'+escaped(key)+'</th>' for key in columns)+'</tr></thead><tbody>'+rows+'</tbody></table></html>')
