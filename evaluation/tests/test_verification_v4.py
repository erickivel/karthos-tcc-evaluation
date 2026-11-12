import copy
import unittest
try:
    from evaluation import verification_v4 as v
except ImportError:v=None


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(v,'separate protocol4 verdicts are missing')
        self.report=dict(state='fail',manifest={'mode':'native','phase':'complete'},
             native_evidence={'complete':True},native_boundaries={'valid':True,'state':'pass'},generator_artifacts={'valid':True},
             generator={'requests':10,'unexpected_outcomes':10,'job_failed':False,'dropped_iterations':None},
             capture={'valid':True,'state':'pass'},source_valid=False,oracle_valid=False,
             source={'requests':None},vm={'requests':None},resources={'state':'pass'},
             counts_path={},duration={},stage_table=[],reasons=[])

    def test_native_complete_is_descriptive_and_outcomes_are_unclassified(self):
        result=v.assemble_verdicts(self.report,{'valid':True})
        self.assertEqual(result['state'],'descriptive')
        self.assertEqual(result['execution']['state'],'complete')
        self.assertTrue(result['evidence']['valid'])
        self.assertEqual(result['application_outcomes']['unclassified'],10)
        self.assertIsNone(result['application_outcomes']['unexpected'])
        self.assertIsNone(result['generator']['unexpected_outcomes'])

    def test_native_requires_complete_destination_boundary_evidence(self):
        report=copy.deepcopy(self.report)
        report['native_boundaries']={'valid':False,'state':'incomplete'}
        result=v.assemble_verdicts(report,{'valid':True})
        self.assertFalse(result['evidence']['valid'])
        self.assertEqual(result['state'],'incomplete')

    def test_empty_or_invalid_structured_capture_invalidates_evidence(self):
        result=v.assemble_verdicts(self.report,{'valid':False,'errors':['empty raw spans']})
        self.assertFalse(result['evidence']['valid'])
        self.assertFalse(result['oracle_valid'])
        self.assertEqual(result['state'],'incomplete')

    def test_count_disagreement_keeps_failure_but_does_not_claim_same_duration_population(self):
        report=copy.deepcopy(self.report)
        report.update(manifest={'mode':'matched','phase':'complete'},oracle_valid=True,source_valid=True,
            source={'requests':36},vm={'requests':30},
            counts_path={'source_to_vm':{'state':'fail','reference':36,'observed':30}},
            duration={'source_to_vm':{'state':'fail','reference':80,'observed':35}})
        result=v.assemble_verdicts(report,{'valid':True})
        self.assertEqual(result['state'],'fail')
        self.assertEqual(result['counts_path']['source_to_vm']['state'],'fail')
        self.assertEqual(result['duration']['source_to_vm']['reference'],80)
        self.assertEqual(result['duration']['source_to_vm']['state'],'incomplete')
        self.assertEqual(result['duration']['population']['state'],'incomplete')


    def test_duration_population_gating_recomputes_aggregate(self):
        report=copy.deepcopy(self.report)
        report.update(manifest={'mode':'matched','phase':'complete'},oracle_valid=True,source_valid=True,
            source={'requests':100},vm={'requests':99},
            generator=dict(self.report['generator'],unexpected_outcomes=0),
            counts_path={'source_to_vm':{'state':'pass','reference':100,'observed':99}},
            duration={'source_to_vm':{'state':'fail','reference':80,'observed':35}})
        result=v.assemble_verdicts(report,{'valid':True})
        self.assertEqual(result['duration']['source_to_vm']['state'],'incomplete')
        self.assertEqual(result['numerical_agreement']['state'],'incomplete')
        self.assertEqual(result['state'],'incomplete')

    def test_application_outcome_failure_is_not_a_numerical_failure(self):
        report=copy.deepcopy(self.report)
        report.update(manifest={'mode':'matched','phase':'complete'},oracle_valid=True,source_valid=True,
            source={'requests':100},vm={'requests':100},
            counts_path={'source_to_vm':{'state':'pass'}},
            duration={'source_to_vm':{'state':'pass'}})
        result=v.assemble_verdicts(report,{'valid':True})
        self.assertEqual(result['application_outcomes']['state'],'fail')
        self.assertEqual(result['numerical_agreement']['state'],'pass')
        self.assertEqual(result['state'],'fail')


    def test_unpaired_trace_span_cannot_become_passing_evidence(self):
        report=copy.deepcopy(self.report)
        report.update(state='incomplete',manifest={'mode':'matched','phase':'complete'},oracle_valid=True,source_valid=True,
            source={'requests':100,'unpaired':1},vm={'requests':100},
            generator=dict(self.report['generator'],unexpected_outcomes=0),
            counts_path={'source_to_vm':{'state':'pass'}},duration={'source_to_vm':{'state':'pass'}})
        result=v.assemble_verdicts(report,{'valid':True})
        self.assertFalse(result['evidence']['valid'])
        self.assertFalse(result['oracle_valid'])
        self.assertFalse(result['source_valid'])
        self.assertEqual(result['state'],'incomplete')


if __name__=='__main__':unittest.main()
