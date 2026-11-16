import tempfile,unittest
from pathlib import Path
from evaluation.gke import analysis
from evaluation.gke.models import write,sha

class BundleResolutionTests(unittest.TestCase):
    def complete_status(self,final,row):
        raw={key:value for key,value in row['export'].items() if key!='analysis_export'}
        for name,document in (('analysis-status.json',{'analysis':{'state':'complete'},'raw_evidence_export':raw}),
                              ('worker-status.json',{'state':'complete','returncode':0,'workers_stopped':True})):
            path=final.parent/name;write(path,document);row['export']['analysis_export']['members'][name]={'sha256':sha(path)}

    def test_separate_final_report_supersedes_pending_checkpoint_with_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);path=root/'window';write(path/'gke-evaluation.json',{'analysis':{'state':'pending'}})
            raw={'verified':True,'archive_sha256':'a'*64}
            final=path/'analysis/gke-evaluation.json';write(final,{'analysis':{'state':'complete'},'raw_evidence_export':raw})
            row={'id':'window','export':dict(raw,analysis_export={'verified':True,'members':{'gke-evaluation.json':{'sha256':sha(final)}}})}
            self.complete_status(final,row)
            resolved,evidence=analysis._resolve_window_report(root,row)
            self.assertEqual(resolved,final);self.assertEqual(evidence['analysis']['state'],'complete')
            write(final,{'analysis':{'state':'complete'}})
            with self.assertRaisesRegex(ValueError,'checksum'):analysis._resolve_window_report(root,row)

    def test_absent_analysis_does_not_fall_back_to_pending_raw(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);write(root/'window/gke-evaluation.json',{'analysis':{'state':'pending'}})
            row={'id':'window','export':{'analysis_export':{'verified':True,'members':{}}}}
            self.assertEqual(analysis._resolve_window_report(root,row),(None,None))

    def test_final_analysis_must_bind_raw_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);final=root/'window/analysis/gke-evaluation.json';write(final,{'raw_evidence_export':{'verified':True,'archive_sha256':'b'*64}})
            row={'id':'window','export':{'verified':True,'archive_sha256':'a'*64,'analysis_export':{'verified':True,'members':{'gke-evaluation.json':{'sha256':sha(final)}}}}}
            self.complete_status(final,row)
            with self.assertRaisesRegex(ValueError,'binding'):analysis._resolve_window_report(root,row)

    def test_claim_links_point_to_analysis_and_describe_selected_accuracy_scope(self):
        from types import SimpleNamespace
        from evaluation.gke import thesis,report_summary
        from evaluation.tests.test_gke_report_summary import ReportSummaryTests
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);report=ReportSummaryTests().report();write(root/'gke-evaluation.json',report);report_summary.save_summary(root,report)
            ctx=SimpleNamespace(state={},manifest={'schedule':'thesis-completion-v2','matrix':[],'product_pins':{'karthos_revision':'fixed'}})
            row={'id':'w','path':str(root),'execution':'complete','evidence_valid':True,
                 'export':{'verified':True,'uri':'gs://bucket/raw','analysis_export':{'verified':True,'uri':'gs://bucket/analysis'}}}
            claims={item['claim']:item for item in thesis.claim_matrix(ctx,[row],None,None)['claims']}
            self.assertEqual(claims['interval-callers']['artifacts'],['gs://bucket/analysis'])
            selected=claims['representative-numerical-agreement']
            self.assertIn('host and lifecycle',selected['limitations'][0])
            self.assertEqual(claims['host-lifecycle-exact-alignment']['verdict'],'not-applicable')

    def test_failed_authoritative_status_dominates_residual_complete_report(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=root/'window/analysis';raw={'verified':True,'archive_sha256':'a'*64}
            final=base/'gke-evaluation.json';status=base/'analysis-status.json';worker=base/'worker-status.json'
            write(final,{'analysis':{'state':'complete'},'evidence':{'valid':True},'raw_evidence_export':raw})
            write(status,{'analysis':{'state':'failed'},'evidence':{'valid':False},'raw_evidence_export':raw})
            write(worker,{'state':'failed','workers_stopped':True,'returncode':1})
            row={'id':'window','export':dict(raw,analysis_export={'verified':True,'members':{p.name:{'sha256':sha(p)} for p in (final,status,worker)}})}
            path,evidence=analysis._resolve_window_report(root,row)
            self.assertEqual(path,status);self.assertEqual(evidence['analysis']['state'],'failed');self.assertFalse(evidence['evidence']['valid'])

    def test_complete_status_requires_successful_stopped_worker(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=root/'window/analysis';raw={'verified':True,'archive_sha256':'a'*64}
            final=base/'gke-evaluation.json';status=base/'analysis-status.json';worker=base/'worker-status.json'
            write(final,{'analysis':{'state':'complete'},'raw_evidence_export':raw})
            write(status,{'analysis':{'state':'complete'},'raw_evidence_export':raw})
            write(worker,{'state':'failed','workers_stopped':True,'returncode':1})
            row={'id':'window','export':dict(raw,analysis_export={'verified':True,'members':{p.name:{'sha256':sha(p)} for p in (final,status,worker)}})}
            with self.assertRaisesRegex(ValueError,'worker'):analysis._resolve_window_report(root,row)
