import contextlib
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation import runner
from evaluation.gke import readiness_live, setup


METRIC = 'servicegraph.request.count'
START = '2026-11-10T14:00:00Z'
MID = '2026-11-10T14:00:15Z'
END = '2026-11-10T14:01:00Z'
BEFORE = '2026-11-10T13:59:59Z'
AFTER = '2026-11-10T14:01:12Z'
SPEC = {'source': 'mesh', 'demo': 'boutique', 'cell': 'boutique-mesh'}


def sample(value, at, **dimensions):
    return {
        'metric_name': METRIC, 'entity_id': 'frontend-id', 'unit': 'count',
        'edge': {'source_id': 'frontend-id', 'target_id': 'checkout-id'},
        'timestamp': at, 'value': value, 'labels': {
            'source': 'mesh.istio', 'edge_client': 'frontend', 'edge_server': 'checkoutservice',
            'edge_client_id': 'frontend-id', 'edge_server_id': 'checkout-id',
            'http.response.status_code': '200', **dimensions,
        },
    }


def raw(value, at, **dimensions):
    return {'value': value, 'timestamp': at, 'labels': {
        'karthos_source': 'mesh.istio', 'client': 'frontend', 'server': 'checkoutservice',
        'reporter': 'destination', 'http_response_status_code': '200', **dimensions,
    }}


class ReadinessCounterTests(unittest.TestCase):
    def probe(self, baseline, samples, raw_points=(), checkout_calls=3, api_completed=None):
        writes, queries = {}, []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = SimpleNamespace(result=root, write=lambda path, data: writes.update({path.name: data}))
            driver = readiness_live.ProbeDriver(SimpleNamespace(ctx=ctx, _selection=lambda *_: {'state': 'pass'}))
            driver.catalogued.add(SPEC['cell'])

            def api_pages(base, route, query, key):
                queries.append(query)
                visible = baseline if query['to'] == BEFORE else samples
                return {'data': {'query': {'aggregation': query.get('aggregation'), 'timestamp_semantics': 'evaluation'},
                                 'samples': visible if query.get('aggregation') == 'latest' else []}}

            clock = {'now':0}
            def captured(ctx, path, base, params, deadline):
                payload = api_pages(base, '/api/v1/metrics', params, 'samples')
                receipt = {'request':params}
                if api_completed is not None and params['to'] != BEFORE:
                    receipt['api_completed_monotonic'] = api_completed
                    clock['now'] = 200  # storage diagnostics completed after gate
                return payload, receipt
            with (patch.object(runner, 'forward', return_value=contextlib.nullcontext('http://test')),
                  patch.object(runner, 'api_pages', side_effect=api_pages),
                  patch('evaluation.gke.poll_capture.capture', side_effect=captured),
                  patch.object(runner, 'utc', side_effect=[BEFORE, AFTER]),
                  patch.object(setup, 'verify_versions', return_value={}),
                  patch.object(readiness_live.time, 'sleep', side_effect=lambda *_: clock.update(now=200)),
                  patch.object(readiness_live, 'cutoff', return_value=readiness_live.time.time()+readiness_live.RESERVE+120)):
                before = driver._api_baseline(SPEC, root)
                with patch.object(readiness_live.time, 'monotonic', side_effect=lambda: clock['now']):
                    try:
                        driver._api(SPEC, root, START, END, 30, baseline=before,
                                    source_records={'checkout_counter_points': list(raw_points)},
                                    checkout_calls=checkout_calls)
                    except ValueError:
                        pass
        return writes, queries

    def test_cold_probe_queries_latest_and_accepts_corroborated_first_counter(self):
        writes, queries = self.probe([], [sample(3, AFTER)], [raw(3, MID)])
        self.assertIn('api-probe.json', writes)
        self.assertTrue(all(query['aggregation'] == 'latest' for query in queries))
        self.assertEqual(queries[0]['to'], BEFORE)
        self.assertEqual(queries[-1]['from'], START)
        self.assertEqual(queries[-1]['to'], AFTER)
        proof = writes['api-probe.json']['counter_evidence'][0]
        self.assertIsNone(proof['previous'])
        self.assertEqual(proof['raw']['timestamp'], MID)

    def test_evaluation_timestamp_does_not_make_a_carried_counter_current(self):
        writes, _ = self.probe([], [sample(30, AFTER)], [raw(30, BEFORE)])
        self.assertNotIn('api-probe.json', writes)

    def test_counter_visible_at_start_is_a_baseline_even_if_it_arrived_late(self):
        writes, _ = self.probe([sample(8, BEFORE)], [sample(30, AFTER), sample(30, START)])
        self.assertNotIn('api-probe.json', writes)

    def test_unchanged_series_and_unconfirmed_reset_do_not_prove_calls(self):
        for value in (30, 2, 0):
            with self.subTest(value=value):
                writes, _ = self.probe([sample(30, BEFORE)], [sample(value, AFTER)], [raw(value, MID)])
                self.assertNotIn('api-probe.json', writes)

    def test_counter_grows_after_an_observed_reset(self):
        writes, _ = self.probe([sample(30, BEFORE)], [sample(4, AFTER), sample(2, MID)])
        self.assertIn('api-probe.json', writes)

    def test_reset_with_current_producer_epoch_can_pass_on_first_positive_observation(self):
        reset = dict(raw(2, MID), started_at=START)
        writes, _ = self.probe([sample(30, BEFORE)], [sample(2, AFTER)], [reset])
        self.assertIn('api-probe.json', writes)
        proof = writes['api-probe.json']['counter_evidence'][0]
        self.assertEqual(proof['method'], 'reset-corroborated')
        self.assertEqual(proof['previous']['value'], 30)

    def test_same_partition_increases_against_its_observed_baseline(self):
        writes, _ = self.probe([sample(8, BEFORE)], [sample(9, AFTER)])
        self.assertIn('api-probe.json', writes)
        proof = writes['api-probe.json']['counter_evidence'][0]
        self.assertEqual(proof['previous']['value'], 8)

    def test_different_partition_requires_its_own_raw_evidence(self):
        writes, _ = self.probe([sample(30, BEFORE)], [sample(40, AFTER, **{'rpc.grpc.status_code': '0'})],
                               [raw(40, MID, rpc_grpc_status_code='13')])
        self.assertNotIn('api-probe.json', writes)

    def test_other_source_namespace_or_checkout_alias_never_passes(self):
        for labels in ({'source': 'traces.servicegraph'}, {'edge_server': 'checkoutserviceservice'},
                       {'server_namespace': 'unrelated-demo'}):
            with self.subTest(labels=labels):
                writes, _ = self.probe([sample(8, BEFORE, **labels)], [sample(9, AFTER, **labels)])
                self.assertNotIn('api-probe.json', writes)

    def test_storage_diagnostic_delay_does_not_change_in_time_api_gate(self):
        # The response completion is authoritative even when diagnostic VM work
        # consumes the rest of the thirty-second readiness gate.
        writes, _ = self.probe([sample(8, BEFORE)], [sample(9, AFTER)], api_completed=29)
        self.assertIn('api-probe.json', writes)
        writes, _ = self.probe([sample(8, BEFORE)], [sample(9, AFTER)], api_completed=31)
        self.assertNotIn('api-probe.json', writes)
        self.assertTrue(writes['api-probe-diagnostics.json']['delayed_activity'])

    def test_failed_poll_keeps_payload_and_rejections(self):
        writes, _ = self.probe([sample(30, BEFORE)], [sample(30, AFTER)])
        self.assertIn('poll-0001.json', writes)
        self.assertEqual(writes['poll-0001.json']['response']['data']['samples'][0]['value'], 30)
        self.assertIn('unchanged-value', str(writes['poll-0001.json']['rejections']))
        self.assertIn('api-probe-diagnostics.json', writes)

    def test_identity_failures_have_explicit_reasons(self):
        writes, _ = self.probe([], [sample(3, AFTER, source='other')])
        self.assertIn('source', str(writes['poll-0001.json']['rejections']))

    def test_current_checkout_calls_are_required(self):
        writes, _ = self.probe([], [sample(3, AFTER)], [raw(3, MID)], checkout_calls=0)
        self.assertNotIn('api-probe.json', writes)

    def test_raw_capture_keeps_only_fresh_exact_selected_cumulative_points(self):
        def point(value, at, **labels):
            row = raw(value, at, **labels)
            return {'timeUnixNano': str(int(readiness_live.stamp(at) * 1e9)), 'asInt': str(value),
                    'attributes': [{'key': name, 'value': {'stringValue': val}}
                                   for name, val in row['labels'].items()]}

        def metric(temporality, points):
            return {'name': 'istio_requests_total',
                    'sum': {'aggregationTemporality': temporality, 'isMonotonic': True, 'dataPoints': points}}

        body = {'resourceMetrics': [{'scopeMetrics': [{'metrics': [
            metric(2, [point(2, BEFORE), point(3, MID), point(4, MID, server='cartservice'),
                       point(5, MID, karthos_source='traces.servicegraph'), point(0, MID)]),
            metric(1, [point(6, MID)]),
        ]}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            args = []
            def kube(*values, **options):
                args.extend(values)
                self.assertTrue(options['binary'])
                lines = json.dumps(body)+'\n{"resourceMetrics":\n'
                return SimpleNamespace(stdout=gzip.compress(lines.encode()))
            ctx = SimpleNamespace(result=Path(tmp), kube=kube)
            driver = readiness_live.ProbeDriver(SimpleNamespace(ctx=ctx))
            before = {'gateway:metrics.json': {'uid': 'old', 'bytes': 100}}
            after = {'gateway:metrics.json': {'uid': 'new', 'bytes': 200, 'inode': 22, 'pod': 'new-gateway'}}
            actual = driver._checkout_points(SPEC, before, after, START)
        self.assertEqual([(row['value'], row['timestamp']) for row in actual], [(3, readiness_live.stamp(MID))])
        self.assertEqual(args[-5:], ['1', '/diag/metrics.json', 'istio_requests_total', '200', '22'])
