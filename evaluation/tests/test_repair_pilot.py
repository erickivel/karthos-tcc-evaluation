import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from collections import Counter

try:
    from evaluation import repair_pilot as repair
except ImportError:
    repair=None


class RepairPilotTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(repair,'protocol4 campaign is not implemented')

    def test_exact_matrix_and_diagnostic_separation(self):
        rows=repair.schedule()
        self.assertEqual(len(rows),32)
        self.assertEqual(Counter(r['phase'] for r in rows),{'smoke':8,'measured':20,'diagnostics':4})
        baseline=[r for r in rows if r['phase']=='measured' and r['spec']['workload']!='native']
        self.assertEqual(len(baseline),16)
        self.assertTrue(all(r['spec']['duration_seconds']==300 and r['spec']['rate']==2 for r in baseline))
        native=[r for r in rows if r['spec']['workload']=='native']
        self.assertEqual(len(native),4)
        self.assertTrue(all(r['cell'].repeat==1 and r['spec']['duration_seconds']==180 for r in native))
        diagnostics=[r for r in rows if r['phase']=='diagnostics']
        self.assertTrue(all(r['cell'].source=='trace' and r['spec']['rate']==4 for r in diagnostics))
        self.assertGreater(min(rows.index(r) for r in diagnostics),max(rows.index(r) for r in baseline))

    def test_generator_order_reverses_by_cell_identity_not_position(self):
        rows=repair.schedule()
        for demo in ('boutique','astronomy'):
            for source in ('trace','mesh'):
                orders=[]
                for repeat in (1,2):
                    orders.append([r['spec']['generator'] for r in rows if r['phase']=='measured' and r['cell'].demo==demo and r['cell'].source==source and r['cell'].repeat==repeat and r['spec']['workload']!='native'])
                self.assertEqual(orders[0],list(reversed(orders[1])))

    def test_declared_window_is_not_extended_to_late_completion(self):
        bounds=repair.window_bounds('2026-01-01T00:00:00Z','2026-01-01T00:05:08Z',300,
              [{'started_at':'2026-01-01T00:04:59Z','completed_at':'2026-01-01T00:05:07Z'}])
        self.assertEqual(bounds['end'],'2026-01-01T00:05:00Z')
        self.assertEqual(bounds['completion_tail_seconds'],8)
        self.assertEqual(bounds['in_flight_completions'],1)

    def test_mechanism_only_is_not_a_resolved_original_cause(self):
        issues=repair.readiness_issues({'astronomy_503':{'state':'mechanism_established_cause_open',
             'mechanism':'Envoy UC upstream connection termination'}})
        self.assertTrue(any('503' in issue for issue in issues))


    def test_counter_priming_is_after_instrumentation_and_outside_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            window=Path(tmp)/'window';window.mkdir()
            cell=repair.Cell('astronomy','trace',1)
            with patch('evaluation.semantics_pilot.run_warmup',return_value={'state':'complete','requests':52}) as warm:
                result=repair.prime_after_instrumentation(cell,window,'study',Path(tmp))
            self.assertEqual(warm.call_args.args,(cell,window,'study:capture-priming'))
            self.assertEqual(warm.call_args.kwargs['image_lock_root'],Path(tmp))
            self.assertEqual(result['purpose'],'counter_priming_after_collector_restart')
            self.assertFalse(result['included_in_measurement'])
            self.assertTrue((window/'counter-priming.json').exists())


    def test_in_place_verification_rejects_historical_protocol(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            window=root/'measured/r1-astronomy-trace/windows/k6-checkout-heavy'
            window.mkdir(parents=True)
            manifest=window/'manifest.json';manifest.write_text(json.dumps({'protocol_version':3}))
            before=manifest.read_bytes()
            with self.assertRaisesRegex(ValueError,'historical'):
                repair.verify_run(root)
            self.assertEqual(manifest.read_bytes(),before)
            self.assertFalse((window/'verification.json').exists())
            self.assertFalse((root/'summary.json').exists())


if __name__=='__main__':unittest.main()
