import unittest
from evaluation.observers import observer_config


class BackgroundTests(unittest.TestCase):
    def test_observer_records_user_agent_without_metric_labels(self):
        self.assertIn('$http_user_agent', observer_config('frontend',8080))

    def test_observed_partitions_preserve_unknown_and_do_not_infer_counts(self):
        from evaluation.gke.background_traffic import classify
        declarations=[dict(pod='p', path='/health', port=8080)]
        events=[dict(pod='p',uri='/health',user_agent='kube-probe/1.34'),
                dict(pod='p',uri='/health'),dict(pod='p',uri='/other',user_agent='kube-probe/1.34'),
                dict(pod='p',uri='/cart',run_id='run',request_id='r1')]
        result=classify(events,declarations,'run')
        self.assertEqual(result['counts'],dict(observed=4,journey_requests=1,identified_probe_requests=1,unclassified_requests=2))
        self.assertFalse(result['whole_cluster_coverage'])
        self.assertIsNone(classify([],declarations,'run')['counts'])
