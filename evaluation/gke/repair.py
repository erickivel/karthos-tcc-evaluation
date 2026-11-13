"""Historical provenance for a new repair run; no imported verdicts or passes."""
import re
from .models import read,sha


def origin_evidence(report,seal,uri,matrix):
    if not report or not seal or not uri:raise ValueError('repair requires original sealed report and GCS report URI')
    if not re.fullmatch(r'gs://[^/]+/gke-qualification-v1/runs/[a-z0-9-]+/[^\s]+/run-report.json',uri):raise ValueError('invalid origin report URI')
    entries={name:digest for digest,name in (line.split('  ',1) for line in seal.read_text().splitlines())}
    if entries.get('run-report.json')!=sha(report):raise ValueError('origin report seal mismatch')
    doc=read(report);old={row['id']:row for row in doc.get('records',[])}
    if doc.get('protocol')!='thesis-qualification-v4':raise ValueError('origin protocol differs')
    for row in matrix:
        previous=old.get(row['origin_id'])
        if not previous:raise ValueError('original logical row unavailable')
        if row['phase']=='baseline' and previous.get('state') not in ('failed','unattempted'):
            raise ValueError('repair baseline is neither failed nor unattempted')
    return {'report_uri':uri,'report_sha256':sha(report),'seal_sha256':sha(seal),
            'run_id':uri.split('/runs/',1)[1].split('/',1)[0],
            'interpretation':'historical provenance only; no passes imported; repaired revision has one cluster'}
