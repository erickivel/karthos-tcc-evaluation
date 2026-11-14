import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from evaluation.gke import local_kind_qualification as q

class LocalKindTests(unittest.TestCase):
    def capture(self, root):
        product=root/'product';product.mkdir()
        lock=root/'lock.json';lock.write_text(json.dumps({'karthos_revision':'a'*40}))
        with patch.object(q,'_product',return_value=([Path('fixture')],[],'a'*40,[])), \
             patch.object(q.shutil,'which',return_value='/fixture/kubectl'):
            return q.KindCapture(product,lock,root/'evidence','karthos-thesis-fixture')

    def evidence_bytes(self, path):
        return {str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.is_file()}

    def test_rejected_existing_output_preserves_all_prior_evidence(self):
        for clusters in ('','karthos-thesis-fixture\n'):
            with self.subTest(clusters=clusters),tempfile.TemporaryDirectory() as folder:
                capture=self.capture(Path(folder));capture.out.mkdir()
                (capture.out/'kind-report.json').write_text('{"state":"pass","original":true}\n')
                (capture.out/'prior.log').write_bytes(b'original evidence\n')
                before=self.evidence_bytes(capture.out)
                with patch.object(q.subprocess,'check_output',return_value=clusters), \
                     patch.object(capture,'run') as command:
                    report=capture.execute()
                self.assertEqual(report['state'],'incomplete')
                self.assertEqual(self.evidence_bytes(capture.out),before)
                command.assert_not_called()

    def test_existing_cluster_does_not_create_output_or_run_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            capture=self.capture(Path(folder))
            with patch.object(q.subprocess,'check_output',return_value=capture.name+'\n'), \
                 patch.object(capture,'run') as command:
                report=capture.execute()
            self.assertIn('existing kind cluster',report['error'])
            self.assertFalse(capture.out.exists())
            command.assert_not_called()

    def test_owned_setup_failure_is_recorded_but_reexecution_cannot_overwrite_it(self):
        with tempfile.TemporaryDirectory() as folder:
            capture=self.capture(Path(folder))
            with patch.object(q.subprocess,'check_output',return_value=''), \
                 patch.object(q.shutil,'copytree',side_effect=OSError('fixture copy failed')):
                report=capture.execute()
                self.assertIn('fixture copy failed',report['error'])
                self.assertEqual(json.loads((capture.out/'kind-report.json').read_text()),report)
                before=self.evidence_bytes(capture.out)
                repeated=capture.execute()
            self.assertIn('File exists',repeated['error'])
            self.assertEqual(self.evidence_bytes(capture.out),before)

    def test_partial_profile_requires_actual_complete_business_mesh(self):
        profile={'capabilities':[{'source_id':'mesh.istio','status':'partial'},{'source_id':'traces.servicegraph','status':'unavailable'}]}
        with self.assertRaisesRegex(ValueError,'business'):
            q.business_profile(profile,[])
        checks=[{'app':name,'proxy_ready':True,'counter_series':1} for name in q.BUSINESS_APPS]
        result=q.business_profile(profile,checks)
        self.assertEqual(result['capabilities'][0]['status'],'available')
        self.assertEqual(profile['capabilities'][0]['status'],'partial')

    def test_parse_real_responses_rejects_missing_calls_and_wrong_results(self):
        rows=['x '+__import__('json').dumps({'gateway':'gateway-go','mode':'intentional-error' if i%4==3 else 'normal','results':[{'status':500 if i%4==3 else 200}]}) for i in range(40)]
        self.assertEqual(len(q.responses('\n'.join(rows))),40)
        with self.assertRaises(ValueError):q.responses('\n'.join(rows[:-1]))
        rows[0]=rows[0].replace('200','500')
        with self.assertRaises(ValueError):q.responses('\n'.join(rows))

    def test_counter_delta_rejects_extra_source_or_reset(self):
        self.assertEqual(q.account_delta({'mesh.istio':2},{'mesh.istio':42},'mesh.istio'),40)
        with self.assertRaises(ValueError):q.account_delta({'mesh.istio':2},{'mesh.istio':42,'traces.servicegraph':40},'mesh.istio')
        with self.assertRaises(ValueError):q.account_delta({'mesh.istio':42},{'mesh.istio':2},'mesh.istio')

if __name__=='__main__':unittest.main()
