import copy
import unittest
from evaluation.gke import population as p
from evaluation.tests import test_offline_population as fixtures


class SettledDurationTests(unittest.TestCase):
    def fixture(self):
        t,v=fixtures.PopulationTests().fixture()
        native=copy.deepcopy(v['source']['series'][0]);stored=copy.deepcopy(v['vm']['series'][0]);native['first']=8;native['last']=23;stored['first']=8;stored['last']=23
        src=[native,dict(native,metric='istio_request_duration_milliseconds_count'),dict(native,metric='istio_request_duration_milliseconds_sum',increase=20)]
        vm=[stored,dict(stored,metric=dict(stored['metric'],__name__='servicegraph_request_duration_count')),dict(stored,increase=.020,metric=dict(stored['metric'],__name__='servicegraph_request_duration_sum',karthos_unit='seconds'))]
        return t,src,vm

    def test_settled_duration_checks_its_population_and_original_workload(self):
        t,src,vm=self.fixture();r=p.settled_duration(t,src,vm,[8,23],{})
        self.assertEqual(r['state'],'pass',r);self.assertEqual(r['source_mean_ms'],20);self.assertEqual(r['stored_mean_ms'],20)
        self.assertEqual(r['fixed_workload_bounds'],[10,20]);self.assertEqual(r['recorded_quiet_bounds'],[8,23])

    def test_unsynchronized_histograms_and_extra_calls_do_not_pass(self):
        t,src,vm=self.fixture();src[-1]['last']=24
        self.assertEqual(p.settled_duration(t,src,vm,[8,23],{})['state'],'insufficient')
        t,src,vm=self.fixture();t['events']['observers'].append(dict(t['events']['observers'][0],identity='extra',start=21,completion=22))
        self.assertEqual(p.settled_duration(t,src,vm,[8,23],{})['state'],'insufficient')

    def test_numerical_disagreement_and_units_are_not_hidden(self):
        t,src,vm=self.fixture();vm[-1]['increase']=1
        self.assertEqual(p.settled_duration(t,src,vm,[8,23],{})['state'],'fail')
        vm[-1]['metric']['karthos_unit']='milliseconds'
        self.assertEqual(p.settled_duration(t,src,vm,[8,23],{})['state'],'insufficient')

    def test_missing_quiet_export_is_separate_from_frozen_store_population(self):
        t,src,vm=self.fixture();before=copy.deepcopy(vm)
        result=p.settled_diagnostics(t,src,[],vm,[8,23],{})
        self.assertEqual(result['quiet_to_quiet']['state'],'insufficient')
        self.assertEqual(result['native_settled_to_frozen_store']['state'],'pass')
        self.assertEqual(vm,before)
        self.assertEqual(result['native_settled_to_frozen_store']['fixed_workload_bounds'],[10,20])

    def test_recorded_export_horizon_never_searches_for_agreeing_values(self):
        from evaluation.gke.accuracy_reanalysis import stored_export_counter
        r=stored_export_counter([(8,10),(21,11),(22,12),(50,99)],8,22,20,45)
        self.assertEqual(r['last'],22);self.assertEqual(r['increase'],2)
        self.assertIsNone(stored_export_counter([(8,10),(19,11)],8,22,20,45)['increase'])
