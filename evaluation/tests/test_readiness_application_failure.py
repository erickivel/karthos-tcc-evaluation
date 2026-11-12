import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from evaluation.gke.models import write,read
from evaluation.gke import readiness_live


class ApplicationFailureEvidenceTests(unittest.TestCase):
    def test_restart_preserves_termination_and_previous_log_before_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);capture=root/'capture';capture.mkdir();case=root/'case';case.mkdir()
            before={'items':[{'metadata':{'name':'checkout-1','uid':'pod-uid'},'status':{'containerStatuses':[{'name':'checkout','restartCount':0}]}}]}
            after={'items':[{'metadata':{'name':'checkout-1','uid':'pod-uid'},'status':{'containerStatuses':[{'name':'checkout','restartCount':1,'lastState':{'terminated':{'exitCode':2,'reason':'Error'}}}]}}]}
            write(capture/'pods-before-demo.json',before)
            calls=[]
            def kube(*args,**kwargs):
                calls.append(args)
                if args[0]=='get':return SimpleNamespace(stdout=json.dumps(after),stderr='',returncode=0)
                return SimpleNamespace(stdout='panic: checkout failed\n',stderr='',returncode=0)
            ctx=SimpleNamespace(kube=kube,write=write)
            readiness_live.capture_application_failure(ctx,capture,'demo',case)
            evidence=read(case/'application-failure-context.json')
            self.assertEqual(evidence['restarted'][0]['last_state']['terminated']['exitCode'],2)
            self.assertIn('panic: checkout failed',evidence['restarted'][0]['previous_log'])
            self.assertTrue(any(args[0]=='logs' and '--previous' in args for args in calls))


    def test_previous_log_failure_does_not_discard_termination_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);capture=root/'capture';capture.mkdir();case=root/'case';case.mkdir()
            write(capture/'pods-before-demo.json',{'items':[{'metadata':{'name':'checkout-1','uid':'pod-uid'},'status':{'containerStatuses':[{'name':'checkout','restartCount':0}]}}]})
            after={'items':[{'metadata':{'name':'checkout-1','uid':'pod-uid'},'status':{'containerStatuses':[{'name':'checkout','restartCount':1,'lastState':{'terminated':{'exitCode':2}}}]}}]}
            def kube(*args,**kwargs):
                if args[0]=='get':return SimpleNamespace(stdout=json.dumps(after),stderr='',returncode=0)
                raise TimeoutError('previous log timeout')
            readiness_live.capture_application_failure(SimpleNamespace(kube=kube,write=write),capture,'demo',case)
            evidence=read(case/'application-failure-context.json')
            self.assertEqual(evidence['restarted'][0]['last_state']['terminated']['exitCode'],2)
            self.assertEqual(evidence['restarted'][0]['previous_log_state'],'unavailable')


    def test_log_discarded_if_same_pod_restarts_again_during_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);capture=root/'capture';capture.mkdir();case=root/'case';case.mkdir()
            def pod(count):return {'items':[{'metadata':{'name':'checkout-1','uid':'pod-uid'},'status':{'containerStatuses':[{'name':'checkout','restartCount':count,'lastState':{'terminated':{'exitCode':2}}}]}}]}
            write(capture/'pods-before-demo.json',pod(0));snapshots=iter([pod(1),pod(2)])
            def kube(*args,**kwargs):
                if args[0]=='get':return SimpleNamespace(stdout=json.dumps(next(snapshots)),stderr='',returncode=0)
                return SimpleNamespace(stdout='wrong prior instance',stderr='',returncode=0)
            readiness_live.capture_application_failure(SimpleNamespace(kube=kube,write=write),capture,'demo',case)
            item=read(case/'application-failure-context.json')['restarted'][0]
            self.assertNotIn('previous_log',item)
            self.assertIn('changed',item['previous_log_state'])


    def test_failure_context_is_retained_in_compact_case_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);case=root/'cases/fixture';case.mkdir(parents=True)
            write(case/'application-failure-context.json',{'state':'captured','restarted':[{'container':'checkout','last_state':{'terminated':{'exitCode':2}}}]})
            driver=readiness_live.ProbeDriver.__new__(readiness_live.ProbeDriver)
            driver.root=root;driver.ctx=SimpleNamespace(write=write,run_id='fixture-run')
            driver.publish=lambda path,relative:{'verified':True}
            driver.publish_case({'id':'fixture','phase':'smoke','scenario':'matched'},{'state':'failed'})
            receipt=read(root/'receipts/fixture/receipt.json')
            self.assertEqual(receipt['evidence']['application-failure-context.json']['restarted'][0]['last_state']['terminated']['exitCode'],2)


    def test_unknown_prior_container_is_not_assumed_to_have_zero_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);capture=root/'capture';capture.mkdir();case=root/'case';case.mkdir()
            write(capture/'pods-before-demo.json',{'items':[{'metadata':{'name':'checkout-1','uid':'pod-uid'},'status':{'containerStatuses':[{'name':'checkout'}]}}]})
            after={'items':[{'metadata':{'name':'checkout-1','uid':'pod-uid'},'status':{'containerStatuses':[{'name':'checkout','restartCount':1}]}}]}
            calls=[]
            def kube(*args,**kwargs):
                calls.append(args)
                return SimpleNamespace(stdout=json.dumps(after),stderr='',returncode=0)
            readiness_live.capture_application_failure(SimpleNamespace(kube=kube,write=write),capture,'demo',case)
            evidence=read(case/'application-failure-context.json')
            self.assertEqual(evidence['restarted'],[])
            self.assertEqual(evidence['unknown_prior'][0]['container'],'checkout')
            self.assertEqual(evidence['state'],'partial')
            self.assertFalse(any(args[0]=='logs' for args in calls))

if __name__=='__main__':unittest.main()
