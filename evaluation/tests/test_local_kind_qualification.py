import unittest
from evaluation.gke import local_kind_qualification as q

class LocalKindTests(unittest.TestCase):
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
