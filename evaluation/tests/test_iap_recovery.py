import json,unittest
from types import SimpleNamespace


class IAPTests(unittest.TestCase):
    def operator(self):
        class Fake:
            args=SimpleNamespace(project='p',zone='z',controller='c',owner_id='o',run_id='r',uid='1')
            calls=[]
            def instance(self):
                return {'id':'1','networkInterfaces':[{'network':'net'}],'serviceAccounts':[{'email':'controller@p'}]}
            def call(self,*args):
                self.calls.append(args)
                if args[:3]==('compute','instances','list'):return json.dumps([dict(self.instance(),name='c')])
                if args[:3]==('compute','firewall-rules','create'):
                    self.rule={'id':'42','name':args[3],'description':next(a.split('=',1)[1] for a in args if a.startswith('--description='))}
                    return json.dumps(self.rule)
                if args[:3]==('compute','firewall-rules','describe'):return json.dumps(self.rule)
                if args[:3]==('compute','firewall-rules','delete'):self.rule=None;return ''
                if args[:3]==('compute','firewall-rules','list'):return json.dumps([self.rule] if self.rule else [])
        return Fake()

    def test_cleanup_proved_even_when_probe_fails(self):
        from evaluation.gke.iap_recovery import recover
        op=self.operator()
        result=recover(op,lambda _: (_ for _ in ()).throw(RuntimeError('SSH unavailable')))
        self.assertTrue(result['temporary_removal_confirmed'])
        self.assertEqual(result['observation_error'],'SSH unavailable')
        create=next(c for c in op.calls if c[:3]==('compute','firewall-rules','create'))
        self.assertIn('--source-ranges=35.235.240.0/20',create)
        self.assertIn('--allow=tcp:22',create)
        self.assertIn('--target-service-accounts=controller@p',create)

    def test_foreign_same_service_account_blocks_before_mutation(self):
        from evaluation.gke.iap_recovery import recover
        op=self.operator();original=op.call
        def call(*args):
            if args[:3]==('compute','instances','list'):
                return json.dumps([dict(op.instance(),name='c'),dict(op.instance(),id='2',name='foreign')])
            return original(*args)
        op.call=call
        with self.assertRaisesRegex(ValueError,'shared'):
            recover(op,lambda _: {})
        self.assertFalse(any(c[2]=='create' for c in op.calls))

    def test_changed_rule_is_not_deleted_and_cleanup_is_unconfirmed(self):
        from evaluation.gke.iap_recovery import recover
        op=self.operator();op.calls=[]
        def probe(operator):
            operator.rule['description']='foreign'
            return {}
        result=recover(op,probe)
        self.assertFalse(result['temporary_removal_confirmed'])
        self.assertIn('ownership changed',result['cleanup_error'])
        self.assertFalse(any(c[2]=='delete' for c in op.calls))

    def test_receipt_survives_failed_cleanup(self):
        import tempfile
        from pathlib import Path
        from evaluation.gke.iap_recovery import recover
        op=self.operator();original=op.call
        def call(*args):
            if args[:3]==('compute','firewall-rules','delete'):raise RuntimeError('denied')
            return original(*args)
        op.call=call
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'receipt.json'
            result=recover(op,lambda _: {},path)
            saved=json.loads(path.read_text())
            self.assertEqual(saved,result)
            self.assertFalse(saved['temporary_removal_confirmed'])
            self.assertEqual(saved['cleanup_error'],'denied')

    def test_cli_cleanup_failure_returns_failure_exit_and_keeps_json(self):
        import contextlib,io
        from unittest.mock import patch
        from evaluation.gke.cli import main
        output=io.StringIO()
        with patch('evaluation.gke.cli.dispatch',return_value={'temporary_removal_confirmed':False,'cleanup_error':'denied'}),contextlib.redirect_stdout(output):
            code=main(['recover-iap','--project','p','--zone','z','--controller','c','--owner-id','o','--run-id','r','--uid','1','--receipt','unused.json'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(output.getvalue())['cleanup_error'],'denied')
