import unittest
from urllib.parse import parse_qs, urlparse
from evaluation.workloads import common

class WorkloadTests(unittest.TestCase):
    def test_request_trace_context_is_stable_distinct_and_valid(self):
        self.assertTrue(hasattr(common,'trace_context'),'trace correlation helper is missing')
        first=common.trace_context('run:1:0')
        self.assertEqual(first,common.trace_context('run:1:0'))
        self.assertNotEqual(first,common.trace_context('run:1:1'))
        self.assertRegex(first['traceparent'],r'^00-[0-9a-f]{32}-[0-9a-f]{16}-01$')
        self.assertEqual(first['trace_id'],first['traceparent'].split('-')[1])

    def test_long_run_id_produces_bounded_valid_distinct_cart_and_email_identity(self):
        run_id='pilot-v2-20261019-b:r1-boutique-trace:k6-checkout-heavy'
        for demo in ('boutique', 'astronomy'):
            identities=[]
            for window in (run_id, run_id+':r2', 'á:'*100):
                steps=common.journey(demo,'checkout-heavy',4,window)
                again=common.journey(demo,'checkout-heavy',4,window)
                self.assertEqual(steps,again)
                if demo=='boutique':
                    user=steps[0]['headers']['Cookie'].split('=',1)[1]
                    email=parse_qs(steps[-1]['body'])['email'][0]
                    self.assertTrue(all(step['headers']['Cookie']==f'shop_session-id={user}' for step in steps))
                else:
                    user=steps[1]['body']['userId']
                    email=steps[-1]['body']['email']
                    self.assertEqual(steps[-1]['body']['userId'],user)
                    self.assertEqual(parse_qs(urlparse(steps[0]['path']).query)['sessionId'],[user])
                self.assertRegex(user,r'^eval-[0-9a-f]{12}-4$')
                self.assertEqual(email,user+'@example.com')
                self.assertLessEqual(len(email.split('@')[0]),64)
                self.assertRegex(email,r'^[a-z0-9-]+@example\.com$')
                identities.append(user)
            self.assertEqual(len(set(identities)),len(identities))

    def test_window_reports_scheduled_iteration_budget_and_actual_end(self):
        row=common.window_record('r','locust','2026-10-19T00:00:00Z',None,30,1)
        self.assertEqual(row['expected_iterations'],30)
        self.assertIsNone(row['ended_at'])
        self.assertEqual(row['intended_duration_seconds'],30)
        self.assertEqual(row['started_at'],'2026-10-19T00:00:00Z')

    def test_legacy_preserves_29_checkouts_in_360_iterations(self):
        for demo in ('boutique', 'astronomy'):
            routes = [s['route'] for n in range(360) for s in common.journey(demo, 'legacy', n, 'r')]
            self.assertEqual(routes.count('checkout'), 29)
            self.assertEqual(routes.count('intentional_error'), 7)

    def test_long_window_has_success_and_real_payment_failure(self):
        for demo in ('boutique', 'astronomy'):
            recipes = [common.journey(demo, 'checkout-heavy', n, 'r') for n in range(10)]
            self.assertEqual(sum(s['route']=='checkout' for r in recipes for s in r), 5)
            failures = [r for r in recipes if r[-1]['route']=='checkout_expired']
            self.assertEqual(len(failures), 1)
            self.assertEqual([s['route'] for s in failures[0]], ['cart_view','cart_add','checkout_expired'])
            body = failures[0][-1]['body']
            self.assertIn('2020', str(body))

    def test_cart_identity_and_currency_follow_checkout_identity(self):
        for demo in ('boutique', 'astronomy'):
            steps = common.journey(demo, 'legacy', 9, 'run')
            self.assertEqual([s['route'] for s in steps], ['cart_view','cart_add','checkout'])
            user = 'eval-acba25512100-9'
            if demo=='boutique':
                self.assertTrue(all(s['headers']['Cookie']==f'shop_session-id={user}' for s in steps))
                self.assertEqual(steps[1]['expected_status'],302)
                self.assertIn('credit_card_number=4432801561520454',steps[2]['body'])
            else:
                self.assertIn(f'sessionId={user}', steps[0]['path'])
                self.assertIn('currencyCode=USD', steps[0]['path'])
                self.assertEqual(steps[1]['body']['userId'],user)
                self.assertEqual(steps[2]['body']['userId'],user)
                self.assertIn('currencyCode=USD',steps[2]['path'])

    def test_checkout_outcome_rejects_empty_orders_and_wrong_failures(self):
        b=common.journey('boutique','legacy',9,'r')[-1]
        self.assertTrue(common.outcome_ok(b,200,'Your order is complete!'))
        self.assertFalse(common.outcome_ok(b,200,'cart'))
        a=common.journey('astronomy','legacy',9,'r')[-1]
        self.assertTrue(common.outcome_ok(a,200,'{"orderId":"o","items":[{"item":{"productId":"OLJCESPC7Z","quantity":1}}]}'))
        self.assertFalse(common.outcome_ok(a,200,'{"orderId":"o","items":[]}'))
        e=common.journey('astronomy','checkout-heavy',9,'r')[-1]
        self.assertTrue(common.outcome_ok(e,422,'{"code":"PAYMENT_FAILED"}'))
        self.assertFalse(common.outcome_ok(e,500,'failed to place order'))

if __name__=='__main__': unittest.main()
