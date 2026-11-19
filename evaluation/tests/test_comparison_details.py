import unittest
from evaluation.gke.comparison_details import details

class ComparisonDetailsTests(unittest.TestCase):
    def test_exact_source_comparison_and_missing_reference(self):
        row=dict(state='pass',observed={'metric':'k8s.pod.volume.available','value':100,'unit':'By'},reference_value=100)
        result=details(row)
        self.assertEqual(result['absolute_error'],0)
        self.assertEqual(result['relative_error'],0)
        self.assertEqual(result['tolerance']['absolute'],1e-9)
        self.assertEqual(result['unit'],'By')
        row.pop('reference_value');row['state']='insufficient'
        self.assertIsNone(details(row)['absolute_error'])
        self.assertIsNone(details(row)['tolerance'])

    def test_no_relative_error_at_zero(self):
        row=dict(state='pass',observed={'value':0,'unit':'count'},reference_value=0)
        self.assertIsNone(details(row)['relative_error'])
