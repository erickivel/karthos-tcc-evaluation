import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from evaluation.gke import models, thesis, thesis_comparison


class CompletionClaimTests(unittest.TestCase):
    def test_four_baselines_can_confirm_answer_without_six_baseline_assumption(self):
        answers={key:{'state':'correct'} for key in ('interval-callers','current-replicas-and-placement','container-resource-history','request-error-derivation','request-error-numerical-agreement')}
        families={key:{'state':'pass'} for key in ('traffic_count','traffic_error','traffic_duration','container_resource_accuracy','volume_accuracy')}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); windows=[]; matrix=[]
            for index in range(4):
                path=root/str(index);path.mkdir();(path/'gke-evaluation.json').write_text('{}')
                report={'evidence':{'valid':True},'execution':{'state':'complete'},'spec':{'phase':'baseline'},
                        'thesis_answers':answers,'coverage':{'contract':{'valid':True}},'families':families,
                        'numerical_agreement':{'state':'insufficient'}}
                (path/'report.json').write_text(json.dumps(report))
                windows.append({'id':str(index),'path':str(path),'evidence_valid':True,'execution':'complete','export':{'verified':True,'uri':'gs://test/'+str(index)}})
                matrix.append({'id':str(index),'phase':'baseline','cell':str(index),'repeat':1})
            ctx=SimpleNamespace(manifest={'schedule':models.COMPLETION,'matrix':matrix,'product_pins':{'karthos_revision':'a'*40}})
            with patch('evaluation.gke.report_summary.load_summary',lambda p:json.loads((p/'report.json').read_text())):
                rows={r['claim']:r for r in thesis.claim_matrix(ctx,windows,None,None)['claims']}
            self.assertEqual(rows['interval-callers']['verdict'],'pass')
            self.assertEqual(rows['representative-numerical-agreement']['verdict'],'pass')
            self.assertEqual(rows['host-lifecycle-exact-alignment']['verdict'],'insufficient')
            self.assertEqual(rows['source-arbitration']['verdict'],'insufficient')
            self.assertFalse(any(name.startswith('clean-rebuild') for name in rows))

    def test_arbitration_requires_both_stages_not_just_current_provenance(self):
        records={name:('gs://'+name,{'source_selection':{'state':'pass','expected_source':source}})
                 for name,source in [('both','mesh.istio'),('trace','traces.servicegraph')]}
        matrix=[{'id':'both','phase':'arbitration','scenario':'both-sources'},
                {'id':'trace','phase':'arbitration','scenario':'replanned-trace'}]
        self.assertEqual(thesis_comparison.arbitration_claim(matrix,records)['verdict'],'insufficient')
        records['trace'][1]['arbitration_transition']={'mode':'rediscover-redeploy','runtime_failover_claim':False,'events':[{'step':step} for step in ['istio-unavailable','rediscovered','redeployed']]}
        self.assertEqual(thesis_comparison.arbitration_claim(matrix,records)['verdict'],'pass')
        del records['both']
        self.assertEqual(thesis_comparison.arbitration_claim(matrix,records)['verdict'],'insufficient')


class CrossDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def manifest(self,name,**updates):
        directory=self.root/name;directory.mkdir()
        data={'run_id':name,'cluster_identity':name,'product_pins':{'karthos_revision':'a'*40,'karthos_source_hash':'b'*64},
              'thresholds':{'request_relative_tolerance':0.01},'baselines':[]}
        data.update(updates);file=directory/'comparison-input.json';models.write(file,data)
        return file

    def test_bound_missing_baselines_remain_insufficient(self):
        left=self.manifest('old');right=self.manifest('new')
        result=thesis_comparison.compare_deployments(left,right,left_sha256=models.sha(left),right_sha256=models.sha(right))
        self.assertEqual(len(result['claims']),2)
        self.assertTrue(all(r['verdict']=='insufficient' for r in result['claims']))
        self.assertFalse(result['historical_passes_imported'])

    def test_rejects_changed_product_scoring_same_cluster_and_checksum(self):
        left=self.manifest('old')
        for name,updates in [('product',{'product_pins':{'karthos_revision':'c'*40,'karthos_source_hash':'b'*64}}),
                             ('scoring',{'thresholds':{}}),('cluster',{'cluster_identity':'old'})]:
            right=self.manifest(name,**updates)
            with self.subTest(name=name),self.assertRaises(ValueError):
                thesis_comparison.compare_deployments(left,right,left_sha256=models.sha(left),right_sha256=models.sha(right))
        right=self.manifest('right')
        with self.assertRaises(ValueError):
            thesis_comparison.compare_deployments(left,right,left_sha256='0'*64,right_sha256=models.sha(right))

    def baseline_manifest(self,name,*,incorrect=False):
        manifest=self.manifest(name);directory=manifest.parent/'baseline';directory.mkdir()
        answers={key:{'state':'correct'} for key in ('interval-callers','current-replicas-and-placement','container-resource-history','request-error-derivation','request-error-numerical-agreement')}
        if incorrect:answers['interval-callers']['state']='incorrect'
        report={'execution':{'state':'complete'},'evidence':{'valid':True},'manifest':{'namespace':'app','source_id':'mesh.istio'},
                'spec':{'phase':'baseline','cell':'boutique-mesh','generator':'k6','rate':2,'duration_seconds':300,'scenario':'matched'},
                'source_selection':{'state':'pass','expected_source':'mesh.istio'},'thesis_answers':answers,
                'traffic':{'report':{'recorder_identity_valid':True,'windows':{'whole':{'checkout_place_order':{'evidence_present':True,'completion':{'requests':100}}}},
                                    'eventual_completeness':{'state':'available','requests':100}}}}
        models.write(directory/'gke-evaluation.json',report)
        nodes=[{'id':key,'kind':kind,'name':node_name,'namespace':namespace} for key,kind,node_name,namespace in
               [('s','service','checkoutservice','app'),('d','workload','checkout','app'),('p','pod','checkout-'+name,'app'),('n','node','node-'+name,'')]]
        models.write(directory/'api-nodes.json',{'data':{'nodes':nodes}})
        models.write(directory/'api-edges.json',{'data':{'edges':[{'source':a,'target':b,'relation':relation} for a,b,relation in
                     [('s','d','exposes'),('d','p','contains'),('p','n','runs_on')]]}})
        models.write(directory/'versions-after.json',{'nodes':[{'name':'node-'+name,'slot':'app-a'}]})
        data=models.read(manifest)
        data['baselines']=[{'cell':'boutique-mesh','directory':'baseline','files':{p.name:models.sha(p) for p in directory.iterdir()}}]
        models.write(manifest,data)
        return manifest

    def test_common_population_passes_and_incorrect_answer_stays_failed(self):
        left=self.baseline_manifest('old');right=self.baseline_manifest('new')
        before=models.sha(left)
        result=thesis_comparison.compare_deployments(left,right,left_sha256=before,right_sha256=models.sha(right))
        mesh=next(row for row in result['claims'] if row['claim']=='clean-rebuild-boutique-mesh')
        self.assertEqual(mesh['verdict'],'pass')
        self.assertEqual(models.sha(left),before)
        third=self.baseline_manifest('incorrect',incorrect=True)
        result=thesis_comparison.compare_deployments(left,third,left_sha256=before,right_sha256=models.sha(third))
        self.assertEqual(next(row for row in result['claims'] if row['claim']=='clean-rebuild-boutique-mesh')['verdict'],'fail')

    def test_member_corruption_and_changed_workload_rejected(self):
        left=self.baseline_manifest('old');right=self.baseline_manifest('new')
        report=right.parent/'baseline/gke-evaluation.json';data=models.read(report);data['spec']['rate']=3
        models.write(report,data)
        with self.assertRaisesRegex(ValueError,'checksum'):
            thesis_comparison.compare_deployments(left,right,left_sha256=models.sha(left),right_sha256=models.sha(right))
        declaration=models.read(right);declaration['baselines'][0]['files']['gke-evaluation.json']=models.sha(report);models.write(right,declaration)
        with self.assertRaisesRegex(ValueError,'workload'):
            thesis_comparison.compare_deployments(left,right,left_sha256=models.sha(left),right_sha256=models.sha(right))

class RepresentativeScopeTests(unittest.TestCase):
    def record(self):
        families={name:{'state':'pass'} for name in ('traffic_count','traffic_error','traffic_duration','container_resource_accuracy','volume_accuracy')}
        families.update(traffic_duration={'state':'insufficient'},settled_traffic_duration={'state':'pass'},
                        evidence_horizon={'state':'pass'},exact_api_storage_parity={'state':'pass'})
        return {'families':families}

    def test_matched_settled_population_can_support_duration_without_changing_fixed_window(self):
        data=self.record()
        self.assertEqual(thesis_comparison.representative_state(data),'pass')
        self.assertEqual(data['families']['traffic_duration']['state'],'insufficient')

    def test_settled_alternative_requires_horizon_and_exact_parity(self):
        for name in ('evidence_horizon','exact_api_storage_parity','settled_traffic_duration'):
            data=self.record();data['families'][name]['state']='insufficient'
            with self.subTest(name=name):
                self.assertEqual(thesis_comparison.representative_state(data),'insufficient')

    def test_original_disagreement_cannot_be_hidden_by_settled_pass(self):
        for name in ('traffic_count','traffic_error','traffic_duration','container_resource_accuracy','volume_accuracy'):
            data=self.record();data['families'][name]['state']='fail'
            with self.subTest(name=name):
                self.assertEqual(thesis_comparison.representative_state(data),'fail')
