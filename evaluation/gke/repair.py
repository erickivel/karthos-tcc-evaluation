"""Historical provenance for a new repair run; no imported verdicts or passes."""
import re
from .models import read,sha


def origin_evidence(report,seal,uri,matrix,*,expected_protocol="thesis-qualification-v4",expected_run_id=None):
    if not report or not seal or not uri:raise ValueError('repair requires original sealed report and GCS report URI')
    if not re.fullmatch(r'gs://[^/]+/gke-qualification-v1/runs/[a-z0-9-]+/[^\s]+/run-report.json',uri):raise ValueError('invalid origin report URI')
    entries={name:digest for digest,name in (line.split('  ',1) for line in seal.read_text().splitlines())}
    if entries.get('run-report.json')!=sha(report):raise ValueError('origin report seal mismatch')
    doc=read(report);old={row['id']:row for row in doc.get('records',[])}
    if doc.get('protocol')!=expected_protocol:raise ValueError('origin protocol differs')
    if expected_run_id and uri.split('/runs/',1)[1].split('/',1)[0]!=expected_run_id:raise ValueError('origin run differs')
    for row in matrix:
        previous=old.get(row['origin_id'])
        if not previous:raise ValueError('original logical row unavailable')
        if expected_protocol=='thesis-qualification-v4' and row['phase']=='baseline' and previous.get('state') not in ('failed','unattempted'):
            raise ValueError('repair baseline is neither failed nor unattempted')
    return {'report_uri':uri,'report_sha256':sha(report),'seal_sha256':sha(seal),
            'run_id':uri.split('/runs/',1)[1].split('/',1)[0],
            'interpretation':'historical provenance only; no passes imported; repaired revision has one cluster'}


def validate_budget(manifest):
    """Explicit cumulative planning allowance; credits never substitute for gross cost."""
    import math
    budget=manifest.get('study_budget',{})
    if budget!=manifest['config'].get('readiness',{}).get('study_budget'):
        raise ValueError('repair combined budget differs from reviewed configuration')
    from .models import COMPLETION_SCHEDULES,COMPLETION_V2
    limit=200 if manifest.get('schedule')==COMPLETION_V2 else 150 if manifest.get('schedule') in COMPLETION_SCHEDULES else 100
    if manifest.get('schedule') in COMPLETION_SCHEDULES and manifest['config'].get('limits',{}).get('gross_limit_usd')!=limit:
        raise ValueError('completion requires the approved USD'+str(limit)+' combined ceiling')
    previous=budget.get('prior_runtime_allowance_usd');combined=budget.get('combined_estimate_usd')
    if (any(type(value) not in (int,float) or not math.isfinite(value) or value<0 for value in (previous,combined))
            or budget.get('gross_limit_usd')!=limit or combined>limit
            or combined+0.005<previous+manifest['cost_preview']['gross_estimate']
            or budget.get('shared_allowances_cumulative') is not True or not budget.get('basis')):
        raise ValueError('study requires prior-runtime allowance plus cumulative shared costs within USD'+str(limit))
