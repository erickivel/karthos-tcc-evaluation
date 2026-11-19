import copy
import unittest
from evaluation.gke.scientific_inputs import agreement


class ScientificInputsTests(unittest.TestCase):
    def manifest(self):
        return dict(product_pins={'revision':'fixed'},image_lock={'product':'digest'},thresholds={'relative':.2},baseline_config={'collection':15},config={'infrastructure':{'kubernetes_version':'fixed','controller_image':'old'},'servicegraph_store_max_items':10000},protocol_definition={'baseline_rate':2,'baseline_seconds':300,'combined_gross_limit_usd':270},implementation_hashes={'evaluation/workloads/run.js':'x','evaluation/workloads/workload.json':'y'})

    def test_operational_image_and_budget_differ_without_masking_science(self):
        left=self.manifest();right=copy.deepcopy(left)
        right['config']['infrastructure']['controller_image']='new'
        right['protocol_definition']['combined_gross_limit_usd']=187.75
        right['protocol_definition']['execution_profile']='boutique-complement-v1'
        self.assertEqual(agreement(left,right)['state'],'pass')
        right['thresholds']['relative']=.3
        self.assertEqual(agreement(left,right)['state'],'fail')

    def test_missing_or_changed_workload_is_not_comparable(self):
        left=self.manifest();right=copy.deepcopy(left)
        right['implementation_hashes']['evaluation/workloads/run.js']='changed'
        self.assertEqual(agreement(left,right)['state'],'fail')
        del right['implementation_hashes']['evaluation/workloads/workload.json']
        self.assertEqual(agreement(left,right)['state'],'insufficient')
