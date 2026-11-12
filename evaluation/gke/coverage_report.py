"""Descriptive coverage populations, separate from numerical agreement.

An observed series is not an independent expectation. Counter differences are
retained as differences, never converted into invented lost request identities.
No sample count is extrapolated from a configured collection cadence.
"""
from __future__ import annotations
import csv
import html
import io
import json
from urllib.parse import quote
from datetime import datetime

SCHEMA_VERSION = 'metric-window-coverage-v1'
COUNTS = ('expected', 'matched', 'missing', 'extra', 'late', 'unassessable')


def _stamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def _identity(sample):
    # Preserve every label, including unknown dimensions and source.
    return {'entity_id': sample.get('entity_id'), 'edge_id': sample.get('edge_id'),
            'source': sample.get('labels', {}).get('source'),
            'dimensions': sample.get('labels', {}), 'unit': sample.get('unit')}


def _key(identity):
    return json.dumps(identity, sort_keys=True, separators=(',', ':'))


def _row(metric, window, scope, **fields):
    return dict({'metric': metric, 'window': window, 'scope': scope,
                 **{key: None for key in COUNTS}, 'coverage_percent': None,
                 'denominator_valid': False}, **fields)


def build(catalog_coverage, traffic, *, start=None, end=None, checks=(), resources=None):
    """Use retained identity evidence without broadening any original verdict.

    Canonical API/storage identity preservation is descriptive. Independent
    resource comparisons certify only their selected measurement, not the
    completeness of a resource's emitted series. These populations stay separate.
    """
    windows = traffic.get('windows', {}) or {'whole': {'bounds_seconds': [start, end]}}
    rows = []
    for window, details in windows.items():
        bounds = details.get('bounds_seconds', [start, end])
        for entry in catalog_coverage.get('entries', []):
            identities = {_key(i): i for i in entry.get('identities', [])}
            reasons = ['independent full-series expectation inventory unavailable',
                       'observed series presence does not establish expected datapoints']
            unsupported = not entry.get('assigned_source')
            if unsupported:
                reasons = ['no deployed source assignment; unsupported population is N/A']
            rows.append(_row(entry['name'], window, 'canonical-series-visibility',
                identity=None, identities=list(identities.values()), bounds_seconds=bounds,
                classification='not-applicable' if unsupported else 'unassessable',
                observed_api_samples=entry.get('api_sample_count', 0) if window=='whole' else None,
                observed_stored_series=entry.get('stored_series_count', 0) if window=='whole' else None,
                accuracy_selected=entry.get('accuracy_selected', False),
                # Legacy catalog classification remains available, but cannot be
                # interpreted as a loss fraction or a fixed subwindow observation.
                legacy_classification=entry.get('classification'),
                observation_scope='extended capture bounds; not subdivided into inner windows',
                unassessable=0 if unsupported else max(1, len(identities)),
                reasons=reasons,
                evidence_links=['extended/api/'+entry['name']+'.json',
                                'extended/vm-raw.ndjson', 'extended/source-plan.json']))
            # Full identity rows are observed query evaluations, not source
            # measurement expectations. Inner-window membership uses only their
            # actual API evaluation timestamps.
            full = {}
            for item in identities.values():
                try:
                    at = _stamp(item.get('evaluation_timestamp'))
                    if bounds[0] is None or bounds[1] is None or not _stamp(bounds[0]) <= at < _stamp(bounds[1]):
                        continue
                except (ValueError, TypeError):
                    if window != 'whole':
                        continue
                identity = _identity({'entity_id':item.get('entity_id'), 'edge_id':item.get('edge_id'),
                                      'labels':item.get('labels',{}), 'unit':item.get('unit')})
                full[_key(identity)] = identity
            for identity in full.values():
                rows.append(_row(entry['name'], window, 'observed-canonical-identity',
                    identity=identity, classification='unassessable', unassessable=1,
                    bounds_seconds=bounds, reasons=['independent complete dimensional expectation unavailable'],
                    evidence_links=['extended/api/'+entry['name']+'.json','extended/vm-raw.ndjson'],
                    limitation='presence at an API evaluation instant; not source measurement coverage'))

    # A successful OR failing aligned comparison has a real matched measurement;
    # failing values must never be recast as missing evidence.
    for index, check in enumerate(checks):
        observed = check.get('observed') or {}
        if not observed.get('metric'):
            continue
        aligned = check.get('state') in ('pass', 'fail')
        identity = {key: observed.get(key) for key in
                    ('node', 'node_uid', 'pod_uid', 'collector_uid', 'source', 'dimensions', 'unit')}
        rows.append(_row(observed['metric'], 'whole', 'selected-reference-alignment',
            identity=identity, classification='matched' if aligned else 'unassessable',
            expected=1 if aligned else None, matched=1 if aligned else None,
            missing=0 if aligned else None, extra=None, late=None,
            unassessable=0 if aligned else 1, denominator_valid=aligned,
            coverage_percent=100.0 if aligned else None,
            numerical_state=check.get('state'), reasons=[] if aligned else [check.get('reason', 'alignment unavailable')],
            evidence_links=['gke-evaluation.json#/numerical_agreement/checks/'+str(index)],
            limitation='selected measurement alignment only; no resource or series population completeness claim'))
    rows.extend(_resource_rows(resources or {}, catalog_coverage))
    rows.extend(_traffic_rows(traffic))
    return {'schema_version': SCHEMA_VERSION, 'rows': rows,
            'traffic_accounting': {key: traffic.get(key) for key in
                ('whole_capture_source_identity_coverage', 'frontend_terminal_identity_coverage',
                 'eventual_completeness', 'fixed_window_fidelity')},
            'resource_population': resources or {},
            'limitations': ['null counts mean unassessable or inapplicable, never zero',
                'series visibility, selected measurement alignment and request identities are distinct populations',
                'no expected scrape counts are inferred from nominal cadence',
                'endpoint counter differences cannot identify lost requests',
                'inactive resource exclusions remain those of the retained inventory verifier']}


def _resource_rows(resources, catalog):
    """Retain independently aligned resource examples and inactive inventory."""
    names = {row['name'] for row in catalog.get('entries', [])}
    metrics = {kind: next((name for name in ('container.'+kind+'.usage', 'k8s.container.'+kind+'.usage')
                          if name in names), 'container.'+kind+'.usage') for kind in ('cpu', 'memory')}
    rows = []
    for index, check in enumerate(resources.get('checks', [])):
        kind = check.get('metric')
        if kind not in metrics:
            continue
        # The independent same-measurement check establishes this selected
        # measurement even when its numerical value disagrees.
        aligned = check.get('independent_agreement', {}).get('state') in ('pass', 'fail')
        identity = {'container': check.get('container'), 'node_uid': check.get('node_uid'),
                    'pod_uid': check.get('pod_uid'), 'collector_uid': check.get('collector_uid'),
                    'source': 'kubelet', 'measurement_timestamp': check.get('raw_sample_timestamp_ns')}
        rows.append(_row(metrics[kind], 'whole', 'selected-resource-measurement',
            identity=identity, classification='matched' if aligned else 'unassessable',
            expected=1 if aligned else None, matched=1 if aligned else None,
            missing=0 if aligned else None, unassessable=0 if aligned else 1,
            denominator_valid=aligned, coverage_percent=100.0 if aligned else None,
            numerical_state=check.get('state'),
            reasons=[] if aligned else [check.get('reason', 'same-measurement independent resource reference unavailable')],
            evidence_links=['gke-evaluation.json#/resources/checks/'+str(index)],
            limitation='selected inventory/measurement only; full emitted series dimensions and population completeness unestablished'))
    for index, inactive in enumerate(resources.get('not_applicable', [])):
        for metric in metrics.values():
            rows.append(_row(metric, 'whole', 'inactive-resource-inventory',
                identity={'container': inactive.get('container'), 'source': 'kubelet'},
                classification='not-applicable', unassessable=0,
                reasons=[inactive.get('reason', 'resource inactive before measurement')],
                evidence_links=['gke-evaluation.json#/resources/not_applicable/'+str(index)]))
    return rows


def _traffic_rows(traffic):
    capture = traffic.get('whole_capture_source_identity_coverage', {})
    generator = traffic.get('generator_contract', {})
    expected = generator.get('checkout_trace_ids', [])
    observed = capture.get('paired_trace_ids', [])
    source = traffic.get('manifest', {}).get('source_id')
    applicable = source == 'traces.servicegraph'
    valid_expected = isinstance(expected, list) and all(isinstance(i, str) and i for i in expected)
    valid_observed = isinstance(observed, list) and all(isinstance(i, str) and i for i in observed)
    # Expected identity validity is independent of whether those IDs were captured.
    valid = (applicable and generator.get('state') == 'available' and bool(expected)
             and valid_expected and valid_observed
             and capture.get('checkout_trace_ids', expected) == expected
             and (not generator.get('checkout_request_ids') or len(generator['checkout_request_ids']) == len(expected))
             and len(expected) == len(set(expected))
             and not generator.get('conflicting_request_ids')
             and traffic.get('capture_integrity', {}).get('valid') is True
             and not traffic.get('integrity', {}).get('trace_identity_conflicts'))
    expected_set = set(expected) if valid_expected else set()
    observed_set = set(observed) if valid_observed else set()
    rows = []
    for metric in ('servicegraph.request.count', 'servicegraph.request.duration'):
        row = _row(metric, 'whole-capture', 'checkout-source-request-identities',
            identity={'entity_id': None, 'edge_id': None, 'source': source,
                      'dimensions': {'operation': 'checkout/PlaceOrder'}},
            denominator_valid=valid, classification='assessed' if valid else ('not-applicable' if source=='mesh.istio' else 'unassessable'),
            expected=len(expected_set) if valid else None,
            matched=len(expected_set & observed_set) if valid else None,
            missing=len(expected_set - observed_set) if valid else None,
            # Extra identities may include legitimate background calls. Preserve
            # their IDs as out-of-contract observations, never label them losses.
            extra=len(observed_set - expected_set) if valid else None,
            late=None, unassessable=0 if valid or source=='mesh.istio' else 1,
            coverage_percent=100*len(expected_set & observed_set)/len(expected_set) if valid else None,
            missing_identities=sorted(expected_set-observed_set) if valid else [],
            extra_identities=sorted(observed_set-expected_set) if valid else [],
            matched_identities=sorted(expected_set & observed_set) if valid else [],
            reasons=[] if valid else (['mesh counters do not expose per-request trace identity; trace coverage is N/A'] if source=='mesh.istio' else ['trace source assignment or independent expected trace identity/capture integrity contract unavailable']),
            evidence_links=['ledger.ndjson', 'attempts.ndjson', 'structured/gateway/raw',
                            'gke-evaluation.json#/traffic/report/whole_capture_source_identity_coverage'],
            limitation='request-to-paired-source-span coverage only; no proof that metric storage retained each request; late delivery identities unavailable')
        rows.append(row)
    return rows


def render(rows):
    """Small reusable CSV and escaped HTML table for offline analysis."""
    columns = ['window', 'metric', 'scope', *COUNTS, 'coverage_percent',
               'denominator_valid', 'classification', 'identity', 'reasons', 'evidence_links']
    output = io.StringIO(); writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(row[key], sort_keys=True) if isinstance(row.get(key), (dict, list))
                         else row.get(key) for key in columns})
    def cell(value):
        return html.escape('N/A' if value is None else json.dumps(value, sort_keys=True)
                           if isinstance(value, (dict, list)) else str(value))
    def links(values):
        # Only generated local evidence paths become anchors.
        return '<br>'.join('<a href="'+html.escape(quote(value, safe='/#._-'), quote=True)+'">'+html.escape(value)+'</a>'
                           if ':' not in value and not value.startswith('//') else html.escape(value)
                           for value in (values or []))
    body = ''.join('<tr>'+''.join('<td>'+(links(row.get(key)) if key=='evidence_links' else cell(row.get(key)))+'</td>' for key in columns)+'</tr>' for row in rows)
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Metric window coverage</title>'
            '<style>body{font:14px system-ui;margin:24px}table{border-collapse:collapse}td,th{padding:6px;border:1px solid #bbb;text-align:left;vertical-align:top}</style>'
            '<h1>Metric window coverage</h1><p>N/A means no independently valid denominator or inapplicable population. '
            'Request source identities, series visibility and selected reference alignment are separate populations. '
            'Numerical disagreements remain in analysis.json. No sample expectations are inferred from scrape cadence.</p><table><thead><tr>'
            + ''.join('<th>'+html.escape(key)+'</th>' for key in columns)
            + '</tr></thead><tbody>'+body+'</tbody></table></html>')
    return output.getvalue(), page
