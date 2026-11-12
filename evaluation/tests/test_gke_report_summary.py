"""Bounded orchestration records preserve decisions, not duplicated raw payloads."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from evaluation.gke import report_summary
from evaluation.gke.thesis_comparison import _observation
from evaluation.gke.models import write,sha

class ReportSummaryTests(unittest.TestCase):
    def report(self):
        return {'path':'/window','spec':{'phase':'baseline','demo':'boutique'},'manifest':{'namespace':'demo','source_id':'traces.servicegraph'},'execution':{'state':'complete'},'evidence':{'valid':True,'large':'not retained'},'source_selection':{'state':'pass','expected_source':'traces.servicegraph'},'numerical_agreement':{'state':'insufficient','checks':[{'large':'raw'}]},'coverage':{'contract':{'valid':True}},'thesis_answers':{'interval-callers':{'state':'correct','large':'raw'}},'traffic':{'report':{'recorder_identity_valid':True,'windows':{'whole':{'checkout_place_order':{'evidence_present':True,'completion':{'requests':2}}}},'eventual_completeness':{'state':'available','requests':2,'numerical_differences':{'whole':0}},'whole_capture_source_identity_coverage':{'checkout_trace_ids':['t1','t2'],'paired_trace_ids':['t1','other','t2']},'generator_contract':{'checkout_request_ids':['r1','r2'],'conflicting_request_ids':[]}}}}
    def test_same_accounting_decisions_and_exact_raw_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);report=self.report();write(path/'gke-evaluation.json',report)
            summary=report_summary.save_summary(path,report)
            self.assertEqual(_observation(summary),_observation(report))
            self.assertEqual(summary['source_report']['sha256'],sha(path/'gke-evaluation.json'))
            self.assertEqual(summary['numerical_agreement'],{'state':'insufficient'})
            self.assertNotIn('large',summary['evidence'])
            self.assertEqual(summary['thesis_answers']['interval-callers'],{'state':'correct'})
            self.assertEqual(report['evidence']['large'],'not retained')
    def test_invalid_population_remains_invalid(self):
        for change in ('duplicate','unpaired','conflict','missing-identity','invalid-evidence'):
            report=self.report();traffic=report['traffic']['report']
            if change=='duplicate':traffic['whole_capture_source_identity_coverage']['checkout_trace_ids']=['t1','t1']
            elif change=='unpaired':traffic['whole_capture_source_identity_coverage']['paired_trace_ids']=['t1']
            elif change=='conflict':traffic['generator_contract']['conflicting_request_ids']=['r1']
            elif change=='missing-identity':traffic['recorder_identity_valid']=False
            else:report['evidence']['valid']=False
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp);write(path/'gke-evaluation.json',report);summary=report_summary.save_summary(path,report)
                self.assertEqual(_observation(summary),_observation(report),change)
                self.assertIsNone(_observation(summary),change)
    def test_large_raw_report_is_never_parsed_for_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            with (path/'gke-evaluation.json').open('wb') as stream:stream.truncate(32*1024**2)
            with patch('evaluation.gke.report_summary.read',side_effect=AssertionError('large read forbidden')):
                result=report_summary.load_summary(path)
            self.assertEqual(result['state'],'unavailable')
            self.assertIn('bound',result['reason'])
    def test_summary_preferred_over_raw_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);report=self.report();write(path/'gke-evaluation.json',report);report_summary.save_summary(path,report)
            with (path/'gke-evaluation.json').open('wb') as stream:stream.truncate(32*1024**2)
            result=report_summary.load_summary(path)
            self.assertEqual(result['execution']['state'],'complete')
            self.assertEqual(result['report_scope'],'orchestration summary; detailed evidence remains in sealed window archive')
    def test_missing_or_corrupt_summary_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);write(path/'orchestration-summary.json','{')
            result=report_summary.load_summary(path)
            self.assertEqual(result['state'],'unavailable')

    def test_checkpoint_never_embeds_detailed_reports(self):
        from types import SimpleNamespace
        from evaluation.gke import recoverable_run as recovery
        from evaluation.gke.models import read
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);window=root/'window';window.mkdir();report=self.report();write(window/'gke-evaluation.json',report);report_summary.save_summary(window,report)
            with (window/'gke-evaluation.json').open('wb') as stream:stream.truncate(32*1024**2)
            records=[{'id':'w','attempt_id':'w-a1','path':str(window),'export':{'verified':True,'uri':'gs://b/window'}}]
            ctx=SimpleNamespace(result=root,run_id='fixture',manifest={},config={},state={'terraform':{'foundation':{'evidence_bucket':'b'}}},write=write)
            with patch('evaluation.gke.export._upload',return_value={'verified':True,'uri':'gs://b/checkpoint'}):
                receipt=recovery._checkpoint(ctx,records,None,[],1)
            checkpoint=root/'checkpoints/phase-0001/checkpoint.json'
            self.assertTrue(receipt['verified'])
            self.assertLess(checkpoint.stat().st_size,16384)
            self.assertEqual(read(checkpoint)['reports']['w']['numerical_agreement'],{'state':'insufficient'})

    def test_claim_matrix_reads_bounded_summaries(self):
        from types import SimpleNamespace
        from evaluation.gke import thesis
        from evaluation.gke.models import read
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);report=self.report();write(root/'gke-evaluation.json',report);report_summary.save_summary(root,report)
            with (root/'gke-evaluation.json').open('wb') as stream:stream.truncate(32*1024**2)
            ctx=SimpleNamespace(manifest={'schedule':'thesis-qualification-v4','matrix':[],'product_pins':{'karthos_revision':'fixed'}})
            row={'id':'w','path':str(root),'execution':'complete','evidence_valid':True,'export':{'verified':True,'uri':'gs://b/w'}}
            with patch('evaluation.gke.report_summary.read',wraps=read) as reader:
                result=thesis.claim_matrix(ctx,[row],None,None)
            self.assertTrue(result['claims'])
            self.assertEqual([call.args[0].name for call in reader.call_args_list],['orchestration-summary.json'])

    def test_claim_verdicts_identical_with_small_raw_or_compact_inputs(self):
        from types import SimpleNamespace
        from evaluation.gke import thesis
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);report=self.report();report['path']=str(root);write(root/'gke-evaluation.json',report)
            ctx=SimpleNamespace(manifest={'schedule':'thesis-qualification-v4','matrix':[],'product_pins':{'karthos_revision':'fixed'}})
            row={'id':'w','path':str(root),'execution':'complete','evidence_valid':True,'export':{'verified':True,'uri':'gs://b/w'}}
            original=thesis.claim_matrix(ctx,[row],None,None)
            report_summary.save_summary(root,report)
            self.assertEqual(thesis.claim_matrix(ctx,[row],None,None),original)
