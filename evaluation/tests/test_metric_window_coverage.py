import csv
import io
import tempfile
import unittest
from pathlib import Path

from evaluation.gke.coverage_report import build, render
from evaluation.gke.analysis import analyse
from evaluation.gke.models import write, read, sha


class CoverageTests(unittest.TestCase):
    def catalog(self):
        return {'entries': [{'name': 'metric.'+str(i), 'assigned_source': 'source',
                             'api_sample_count': 10, 'stored_series_count': 2,
                             'classification': 'observed', 'identities': []} for i in range(43)]}

    def traffic(self):
        return {'manifest': {'source_id': 'traces.servicegraph'}, 'generator_contract': {'state': 'available', 'checkout_trace_ids': ['a', 'b']},
                'capture_integrity': {'valid': True},
                'whole_capture_source_identity_coverage': {'checkout_trace_ids': ['a', 'b'], 'paired_trace_ids': ['b', 'c']},
                'eventual_completeness': {'requests': 100, 'numerical_differences': {'delta': 99}}}

    def test_all_43_visible_without_inventing_expectations(self):
        result = build(self.catalog(), {}, start=0, end=60)
        self.assertEqual(len(result['rows']), 45)
        for row in result['rows'][:43]:
            self.assertIsNone(row['expected'])
            self.assertIsNone(row['missing'])
            self.assertIsNone(row['coverage_percent'])
            self.assertFalse(row['denominator_valid'])

    def test_valid_missing_identity_is_loss_result_not_invalid_denominator(self):
        traffic = self.traffic(); traffic['independent_valid'] = False
        report = build(self.catalog(), traffic)
        row = report['rows'][-1]
        self.assertEqual([row[k] for k in ('expected', 'matched', 'missing', 'extra')], [2, 1, 1, 1])
        self.assertEqual(row['missing_identities'], ['a'])
        self.assertEqual(row['coverage_percent'], 50)
        self.assertIsNone(row['late'])
        self.assertEqual(report['traffic_accounting']['eventual_completeness']['requests'], 100)

    def test_counter_disagreement_never_identifies_lost_requests(self):
        traffic = self.traffic(); traffic['generator_contract']['state'] = 'incomplete'
        row = build(self.catalog(), traffic)['rows'][-1]
        self.assertIsNone(row['missing'])
        self.assertEqual(row['missing_identities'], [])

    def test_duplicate_expected_ids_are_unassessable(self):
        traffic = self.traffic()
        traffic['whole_capture_source_identity_coverage']['checkout_trace_ids'] = ['a', 'a']
        row = build(self.catalog(), traffic)['rows'][-1]
        self.assertFalse(row['denominator_valid'])
        self.assertIsNone(row['coverage_percent'])

    def test_numeric_failure_remains_matched_measurement(self):
        check = {'state': 'fail', 'observed': {'metric': 'metric.1', 'source': 'src', 'node_uid': 'n',
                                              'dimensions': {'state': 'free', 'unknown': 'kept'}}}
        row = build(self.catalog(), {}, checks=[check])['rows'][-3]
        self.assertEqual(row['numerical_state'], 'fail')
        self.assertEqual((row['expected'], row['matched'], row['missing']), (1, 1, 0))
        self.assertEqual(row['identity']['dimensions']['unknown'], 'kept')

    def test_unsupported_is_na_and_inner_does_not_reuse_whole_observation_counts(self):
        catalog = self.catalog(); catalog['entries'][0]['assigned_source'] = None
        report = build(catalog, {'windows': {'whole': {'bounds_seconds': [0, 60]}, 'inner': {'bounds_seconds': [10, 50]}}})
        self.assertEqual(report['rows'][0]['classification'], 'not-applicable')
        inner = [r for r in report['rows'] if r['window'] == 'inner'][0]
        self.assertIsNone(inner['observed_api_samples'])
        self.assertIsNone(inner['expected'])

    def test_full_dimensions_remain_distinct(self):
        catalog = self.catalog()
        catalog['entries'][0]['identities'] = [
            {'entity_id': 'e', 'unit': 'bytes', 'labels': {'source': 'src', 'state': state}, 'evaluation_timestamp': 20}
            for state in ('free', 'used')]
        rows = build(catalog, {}, start=0, end=60)['rows']
        dimensions = [r['identity']['dimensions'] for r in rows if r['scope'] == 'observed-canonical-identity']
        self.assertEqual(len(dimensions), 2)
        self.assertEqual({d['state'] for d in dimensions}, {'free', 'used'})

    def test_analysis_outputs_versioned_table_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); run = root/'results/gke-qualification-v1/runs/run'
            write(run/'run-report.json', {'state': 'diagnostic', 'windows': [{'id': 'window'}]})
            evidence = run/'window/gke-evaluation.json'
            write(evidence, {'coverage': self.catalog(), 'traffic': {'report': self.traffic()},
                             'numerical_agreement': {'state': 'fail', 'checks': []}})
            original = sha(evidence)
            result = analyse(root, 'run', 'new-analysis')
            dest = Path(result['analysis'])
            self.assertEqual(sha(evidence), original)
            output = read(dest/'analysis.json')
            self.assertEqual(output['windows'][0]['numerical_agreement']['state'], 'fail')
            self.assertEqual(output['coverage_schema_version'], 'metric-window-coverage-v1')
            table = list(csv.DictReader(io.StringIO((dest/'metric-window-coverage.csv').read_text())))
            self.assertTrue(table)
            self.assertIn('<table>', (dest/'metric-window-coverage.html').read_text())

    def test_real_emitted_generator_contract_shapes(self):
        from evaluation.timing_analysis import generator_contract
        from evaluation.workloads.timing_probe_contract import build_plan
        import json
        for demo in ('boutique', 'astronomy'):
            with self.subTest(demo=demo), tempfile.TemporaryDirectory() as d:
                path = Path(d); records = []
                for journey in build_plan(demo, 'fixture'):
                    for step in journey['steps']:
                        records.append(dict(step, run_id='fixture', started_at=step['planned_offset_seconds'],
                                            actual_trace_id='trace-'+step['request_id'], completed=True, outcome_ok=True))
                for name in ('ledger.ndjson', 'attempts.ndjson'):
                    (path/name).write_text(''.join(json.dumps(r)+'\n' for r in records))
                manifest = {'kind': 'probe', 'run_id': 'fixture', 'probe_expected_checkout_operations': 24,
                            'source_id': 'traces.servicegraph'}
                generator = generator_contract(path, manifest, 0, 120)
                expected = generator['checkout_trace_ids']
                traffic = {'manifest': manifest, 'generator_contract': generator,
                           'capture_integrity': {'valid': True},
                           'whole_capture_source_identity_coverage': {
                               'checkout_trace_ids': expected, 'paired_trace_ids': expected[1:]}}
                row = build(self.catalog(), traffic)['rows'][-1]
                self.assertTrue(row['denominator_valid'], generator)
                self.assertEqual((row['expected'], row['matched'], row['missing']), (24, 23, 1))

    def test_mesh_counters_never_become_trace_losses(self):
        traffic = self.traffic(); traffic['manifest']['source_id'] = 'mesh.istio'
        row = build(self.catalog(), traffic)['rows'][-1]
        self.assertEqual(row['classification'], 'not-applicable')
        self.assertIsNone(row['missing'])
        self.assertIsNone(row['expected'])
        self.assertEqual(row['identity']['source'], 'mesh.istio')

    def test_unknown_source_and_malformed_ids_do_not_establish_denominator(self):
        for change in ('unknown', 'malformed'):
            traffic = self.traffic()
            if change == 'unknown':
                traffic.pop('manifest')
            else:
                traffic['generator_contract']['checkout_trace_ids'] = [None, {}]
            row = build(self.catalog(), traffic)['rows'][-1]
            self.assertFalse(row['denominator_valid'])
            self.assertIsNone(row['coverage_percent'])

    def test_inactive_inventory_is_na_and_value_disagreement_stays_matched(self):
        resources = {'checks': [{'metric': 'cpu', 'state': 'fail', 'container': ['ns', 'pod', 'c'],
                                 'independent_agreement': {'state': 'fail'}}],
                     'not_applicable': [{'container': ['ns', 'finished', 'c'], 'reason': 'completed before measurement'}]}
        rows = build(self.catalog(), {}, resources=resources)['rows']
        measured = next(r for r in rows if r['scope'] == 'selected-resource-measurement')
        self.assertEqual(measured['matched'], 1)
        self.assertEqual(measured['numerical_state'], 'fail')
        inactive = [r for r in rows if r['scope'] == 'inactive-resource-inventory']
        self.assertEqual(len(inactive), 2)
        self.assertTrue(all(r['expected'] is None and r['classification'] == 'not-applicable' for r in inactive))

    def test_html_escapes_untrusted_dimensions(self):
        _, page = render([{'metric': '<script>', 'identity': {'x': '<img>'}}])
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
