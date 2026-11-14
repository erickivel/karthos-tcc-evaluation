import json
import tempfile
import unittest
from pathlib import Path
from evaluation import export_horizon as h


class ExportHorizonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)

    def put(self, name, data):
        (self.path/name).write_text(json.dumps(data))

    def test_quiet_and_finalization_extend_export_without_moving_workload(self):
        self.put('manifest.json', {'phase_boundaries': {'drain_completed_at': 200, 'capture_finalized_at': 245}})
        self.put('quiet-after.json', {'finished_at': '1970-01-01T00:04:00+00:00', 'records': []})
        result = h.resolve(self.path, '1970-01-01T00:01:00+00:00', '1970-01-01T00:02:00+00:00')
        self.assertEqual(result['export_end_epoch'], 245)
        self.assertEqual(result['export_start_epoch'], 0)
        self.assertEqual(result['workload_end_epoch'], 120)
        self.assertEqual(result['state'], 'recorded')

    def test_missing_boundary_is_explicit_not_assumed_ninety_seconds(self):
        result = h.resolve(self.path, 60, 120)
        self.assertEqual(result['state'], 'insufficient')
        self.assertEqual(result['export_end_epoch'], 120)

    def test_incomplete_quiet_record_does_not_become_complete_evidence(self):
        self.put('quiet-after.json', {'stable': False, 'finished_at': 250, 'records': []})
        result = h.resolve(self.path, 60, 120)
        self.assertEqual(result['export_end_epoch'], 250)
        self.assertFalse(result['quiet_stable'])

    def test_export_coverage_checks_exact_quiet_partition_timestamp_and_value(self):
        metric = {'__name__': 'servicegraph_request_count', 'source': 'mesh.istio', 'grpc_status_code': '13'}
        expected = {'metric': metric, 'timestamps': [230000], 'values': [12]}
        self.put('quiet-after.json', {'stable': True, 'finished_at': 240, 'records': [{'raw_rows': [expected]}]})
        bounds = h.resolve(self.path, 60, 120)
        self.assertEqual(h.coverage(self.path, json.dumps(expected), bounds)['state'], 'pass')
        wrong = dict(expected, values=[11])
        self.assertEqual(h.coverage(self.path, json.dumps(wrong), bounds)['state'], 'insufficient')
        wrong = dict(expected, metric=dict(metric, grpc_status_code='0'))
        self.assertEqual(h.coverage(self.path, json.dumps(wrong), bounds)['state'], 'insufficient')

    def test_invalid_or_pre_workload_boundary_rejected(self):
        for value in (float('inf'), 'bad', True, 100):
            self.put('manifest.json', {'phase_boundaries': {'capture_finalized_at': value}})
            with self.assertRaises(ValueError): h.resolve(self.path, 60, 120)

    def test_both_exporters_use_shared_horizon(self):
        import inspect
        from evaluation import pilot
        from evaluation.gke import evaluate
        for fn in (pilot.export_window, evaluate.capture_extended):
            source = inspect.getsource(fn)
            self.assertIn('export_horizon', source)
            self.assertNotIn('timedelta(seconds=90)', source)
            self.assertNotIn('_time(end)+90', source)

    def test_microsecond_completion_is_not_rounded_down(self):
        self.put('manifest.json', {'phase_boundaries': {'capture_finalized_at': 245.987654}})
        self.assertEqual(h.resolve(self.path, 60, 120)['export_end_epoch'],245.987654)

    def test_both_exporters_request_late_quiet_population(self):
        import io
        from types import SimpleNamespace
        from unittest.mock import patch
        from contextlib import nullcontext
        from evaluation import pilot, runner as r
        from evaluation.gke import evaluate, parity
        self.put('manifest.json', {'phase_boundaries': {'capture_finalized_at': 245.5}})
        metric={'__name__':'servicegraph_request_count','source':'mesh.istio'}
        row={'metric':metric,'timestamps':[230000],'values':[12]}
        self.put('quiet-after.json',{'stable':True,'finished_at':240,'records':[{'raw_rows':[row]}]})
        requested=[]
        def response(url, **kw):
            requested.append(url)
            return io.BytesIO((json.dumps(row)+'\n').encode())
        services={'items':[{'metadata':{'name':'victoria'}}]}
        def inventory(*args,**kw):return services if 'services' in args else {'items':[]}
        def write(path,data):
            path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(data if isinstance(data,str) else json.dumps(data))
        cell=SimpleNamespace(namespace='app',demo='boutique',source='mesh')
        with patch.object(r,'kube_json',side_effect=inventory), patch.object(r,'forward',side_effect=lambda *a: nullcontext('http://vm')), \
             patch.object(r.urllib.request,'urlopen',side_effect=response), patch.object(r,'api_pages',return_value={}), \
             patch.object(r,'get_json',return_value={}), patch.object(r,'CONFIG',{}), \
             patch.object(r,'http_timeout',side_effect=lambda value:value),patch.object(pilot,'metric_queries',return_value=[]):
            pilot.export_window(cell,self.path,60,120,60)
            ctx=SimpleNamespace(json=inventory,write=write)
            definitions=lambda ctx,name: {'entries':[{'name':'m'+str(i)} for i in range(43)]} if name=='coverage.yaml' else {'metrics':[]}
            with patch.object(evaluate,'_definitions',side_effect=definitions),patch.object(parity,'capture',return_value={}):
                evaluate.capture_extended(ctx,cell,self.path,60,120)
        from urllib.parse import parse_qs,urlparse
        self.assertEqual(len(requested),2)
        for url in requested:
            self.assertEqual(h.epoch(parse_qs(urlparse(url).query)['end'][0]),245.5)
        for prefix in ('','extended/'):
            self.assertEqual(json.loads((self.path/(prefix+'vm-export-coverage.json')).read_text())['state'],'pass')

    def test_interval_export_uses_parent_evidence_not_empty_destination(self):
        import inspect
        from evaluation import timing_pilot, pilot
        self.assertIn('evidence_path=path', inspect.getsource(timing_pilot.export_intervals))
        self.assertIn('evidence_path', inspect.signature(pilot.export_window).parameters)

    def test_conflicting_quiet_endpoints_are_not_resolved_by_largest_value(self):
        metric={'__name__':'servicegraph_request_count'}
        row={'metric':metric,'timestamps':[230000,230000],'values':[11,12]}
        self.put('quiet-after.json',{'stable':True,'finished_at':240,'records':[{'raw_rows':[row]}]})
        bounds=h.resolve(self.path,60,120)
        self.assertEqual(h.coverage(self.path,json.dumps(dict(row,timestamps=[230000],values=[12])),bounds)['state'],'insufficient')

    def test_incomplete_horizon_remains_a_separate_report_family(self):
        from evaluation.gke.report_summary import family_summaries
        families=family_summaries({'evidence_horizon':{'state':'insufficient'},'traffic':{'report':{'api_storage_parity':{'checks':{'request-count':{'state':'pass'},'error-count':{'state':'pass'},'duration':{'state':'pass'}}}}}})
        self.assertEqual(families['evidence_horizon']['state'],'insufficient')
        self.assertEqual(families['exact_api_storage_parity']['state'],'pass')
