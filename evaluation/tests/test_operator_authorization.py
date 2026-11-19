import copy,json,unittest
from types import SimpleNamespace
from unittest.mock import patch


class BootstrapTests(unittest.TestCase):
    def module(self):
        from evaluation.gke import operator_authorization
        return operator_authorization

    def args(self):
        return SimpleNamespace(project='p',zone='z',controller='vm',owner_id='gke-qualification-v1',run_id='run-1',uid='11',cluster='cluster',cluster_uid='22',operator_account='operator@example.com')

    def cluster(self):
        return {'name':'cluster','id':'22','location':'z','status':'RUNNING','resourceLabels':{'evaluation-owner':'gke-qualification-v1','evaluation-run':'run-1'},'network':'projects/p/global/networks/net','privateClusterConfig':{'privateEndpoint':'172.16.0.2','enablePrivateEndpoint':True},'masterAuth':{'clusterCaCertificate':'Y2E='}}

    def vm(self):
        return {'id':'11','networkInterfaces':[{'network':'https://www.googleapis.com/compute/v1/projects/p/global/networks/net'}],'serviceAccounts':[{'email':'controller@p.iam.gserviceaccount.com','scopes':['https://www.googleapis.com/auth/cloud-platform','https://www.googleapis.com/auth/userinfo.email']}]}

    def test_target_validation_rejects_replaced_uid_and_owner(self):
        m=self.module()
        for field,value in [('id','old'),('resourceLabels',{'evaluation-owner':'foreign','evaluation-run':'run-1'}),('location','other')]:
            cluster=self.cluster();cluster[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):m.validate_target(self.args(),cluster,self.vm(),'controller@p.iam.gserviceaccount.com')

    def test_target_requires_private_endpoint_same_network_expected_sa_and_email_scope(self):
        m=self.module();m.validate_target(self.args(),self.cluster(),self.vm(),'controller@p.iam.gserviceaccount.com')
        for mutate in (lambda v:v['networkInterfaces'][0].update(network='projects/p/global/networks/other'),lambda v:v['serviceAccounts'][0].update(email='foreign@p'),lambda v:v['serviceAccounts'][0].update(scopes=['https://www.googleapis.com/auth/cloud-platform'])):
            vm=self.vm();mutate(vm)
            with self.assertRaises(ValueError):m.validate_target(self.args(),self.cluster(),vm,'controller@p.iam.gserviceaccount.com')
        cluster=self.cluster();cluster['privateClusterConfig']['enablePrivateEndpoint']=False
        with self.assertRaises(ValueError):m.validate_target(self.args(),cluster,self.vm(),'controller@p.iam.gserviceaccount.com')

    def test_lease_requires_same_token_generation_run_and_active_deadline(self):
        m=self.module();saved={'token':'secret','generation':'10'};active={'project_id':'p','owner_id':'gke-qualification-v1','run_id':'run-1','token':'secret','phase':'active','deadline':200}
        m.validate_lease(self.args(),saved,active,'10',100)
        for key,value in [('token','other'),('run_id','other'),('phase','cleanup-requested'),('deadline',99)]:
            row=dict(active);row[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):m.validate_lease(self.args(),saved,row,'10',100)
        with self.assertRaises(ValueError):m.validate_lease(self.args(),saved,active,'11',100)

    def test_binding_created_only_after_recheck_and_matching_readback(self):
        m=self.module();expected=m.binding_document(self.args(),'controller@p.iam.gserviceaccount.com');events=[];existing=None
        def api(method,path,body=None):
            nonlocal existing
            events.append(method)
            if method=='GET':
                if existing is None:raise m.APIError(404)
                return existing
            existing=copy.deepcopy(body);existing['metadata']['uid']='binding-uid';return existing
        result=m.ensure_binding(api,expected,lambda:events.append('recheck'))
        self.assertEqual(events,['GET','recheck','POST','GET']);self.assertEqual(result['binding_uid'],'binding-uid')
        self.assertEqual(expected['metadata']['annotations']['evaluation.karthos.dev/cluster-uid'],'22')
        self.assertEqual(expected['roleRef']['name'],'cluster-admin')

    def test_existing_binding_must_match_identity_scope_and_uid(self):
        m=self.module();expected=m.binding_document(self.args(),'controller@p.iam.gserviceaccount.com')
        for mutate in (lambda x:x['subjects'][0].update(name='foreign'),lambda x:x['metadata']['labels'].update({'evaluation.karthos.dev/run-id':'other'}),lambda x:x['metadata']['annotations'].update({'evaluation.karthos.dev/cluster-uid':'old'}),lambda x:x['roleRef'].update(name='other')):
            row=copy.deepcopy(expected);row['metadata']['uid']='uid';mutate(row);calls=[]
            def api(method,path,body=None):calls.append(method);return row
            with self.assertRaises(ValueError):m.ensure_binding(api,expected,lambda:None)
            self.assertEqual(calls,['GET'])

    def test_failed_recheck_never_creates_binding(self):
        m=self.module();calls=[]
        def api(method,path,body=None):calls.append(method);raise m.APIError(404)
        with self.assertRaisesRegex(ValueError,'changed'):m.ensure_binding(api,m.binding_document(self.args(),'controller@p.iam.gserviceaccount.com'),lambda:(_ for _ in ()).throw(ValueError('changed')))
        self.assertEqual(calls,['GET'])

    def test_forbidden_api_response_is_not_treated_as_absence(self):
        m=self.module();calls=[]
        def api(method,path,body=None):calls.append(method);raise m.APIError(403)
        with self.assertRaises(m.APIError):m.ensure_binding(api,m.binding_document(self.args(),'controller@p.iam.gserviceaccount.com'),lambda:None)
        self.assertEqual(calls,['GET'])

    def test_changed_uid_after_create_is_rejected(self):
        m=self.module();expected=m.binding_document(self.args(),'controller@p.iam.gserviceaccount.com');calls=[]
        def api(method,path,body=None):
            calls.append(method)
            if len(calls)==1:raise m.APIError(404)
            result=copy.deepcopy(expected);result['metadata']['uid']='new' if method=='POST' else 'replacement';return result
        with self.assertRaisesRegex(ValueError,'UID'):m.ensure_binding(api,expected,lambda:None)

    def proof_operator(self):
        m=self.module();op=object.__new__(m.BootstrapOperator);op.args=self.args();op.args.inventory_uri='gs://evidence/control/run-1/inventory/'+'a'*64+'.json';op.args.inventory_generation='12'
        op.foundation={'state_bucket':'state','evidence_bucket':'evidence'};op.account='controller@p.iam.gserviceaccount.com';op.config={'limits':{'cleanup_reserve_seconds':50}}
        op.state={'deadline':1000,'lease':{'bucket':'state','object':'control/active-run.json','uri':'gs://state/control/active-run.json','token':'secret','generation':'10'}}
        binding='gs://evidence/control/run-1/uid-bindings/'+'b'*32+'.json'
        name='projects/p/locations/z/clusters/cluster'
        lease={'project_id':'p','owner_id':'gke-qualification-v1','run_id':'run-1','token':'secret','phase':'active','deadline':1000}
        inventory={'project_id':'p','owner_id':'gke-qualification-v1','run_id':'run-1','lease':{'token':'secret'},'deadline':1000,'resources':[{'kind':'cluster','name':name,'uid':'22','binding':{'uri':binding,'generation':'13'}},{'kind':'instance','name':'projects/p/zones/z/instances/vm','uid':'11'}]}
        creation={'kind':'cluster','name':name,'uid':'22','project_id':'p','owner_id':'gke-qualification-v1','run_id':'run-1','lease_token':'secret','intent_id':'b'*32}
        op.docs={op.state['lease']['uri']:(lease,'10'),op.args.inventory_uri:(inventory,'12'),binding:(creation,'13')}
        def read(uri,generation=None):
            doc,actual=op.docs[uri]
            if generation is not None and str(generation)!=actual:raise ValueError('generation changed')
            return doc,actual
        op._read=read;op.instance=self.vm;op.call=lambda *args:json.dumps(self.cluster())
        return op

    def test_fresh_generation_inventory_and_creation_binding_are_both_required(self):
        op=self.proof_operator()
        with patch('evaluation.gke.operator_authorization.time.time',return_value=100):
            self.assertEqual(op.check()[0],'172.16.0.2')
            self.assertEqual(op.creation_proof['binding_generation'],'13')
        for mutation in ('cluster','lease','intent','generation','deadline'):
            op=self.proof_operator();creation=list(op.docs.values())[-1][0]
            if mutation=='cluster':creation['uid']='old'
            elif mutation=='lease':creation['lease_token']='other'
            elif mutation=='intent':creation['intent_id']='c'*32
            elif mutation=='generation':op.args.inventory_generation='11'
            else:op.state['deadline']=200
            with self.subTest(mutation=mutation),patch('evaluation.gke.operator_authorization.time.time',return_value=100),self.assertRaises(ValueError):op.check()

    def test_changed_cluster_ca_before_mutation_is_not_accepted(self):
        # The orchestration checks the target tuple again immediately before POST.
        m=self.module();expected=m.binding_document(self.args(),'controller@p.iam.gserviceaccount.com')
        calls=[]
        def api(method,path,body=None):calls.append(method);raise m.APIError(404)
        with self.assertRaisesRegex(ValueError,'CA'):
            m.ensure_binding(api,expected,lambda:(_ for _ in ()).throw(ValueError('cluster endpoint or CA changed')))
        self.assertEqual(calls,['GET'])

    def test_private_api_rejects_invalid_ca_without_sending_token(self):
        m=self.module()
        with self.assertRaises(ValueError):m.PrivateAPI('172.16.0.2','Y2E=',1234,'secret-token')

    def test_tls_failure_closes_transport_without_sending_bearer(self):
        import ssl
        from unittest.mock import MagicMock
        m=self.module();api=object.__new__(m.PrivateAPI);api.endpoint='172.16.0.2';api.port=1234;api.token='secret-token';api.remaining=lambda:30;api.context=MagicMock();api.context.wrap_socket.side_effect=ssl.SSLError('certificate rejected')
        conn=MagicMock();transport=MagicMock()
        with patch.object(m.http.client,'HTTPSConnection',return_value=conn),patch.object(m.socket,'create_connection',return_value=transport),self.assertRaises(ssl.SSLError):api('GET',m.RBAC)
        transport.close.assert_called_once();conn.request.assert_not_called();conn.close.assert_called_once();api.context.wrap_socket.assert_called_once_with(transport,server_hostname='172.16.0.2')

    def test_tunnel_startup_failure_stops_process_group(self):
        from unittest.mock import MagicMock
        m=self.module();op=SimpleNamespace(args=self.args(),env={},remaining=lambda:45);process=MagicMock();process.pid=12345;process.poll.return_value=1
        reserved=MagicMock();reserved.__enter__.return_value.getsockname.return_value=('127.0.0.1',1234)
        with patch.object(m.socket,'socket',return_value=reserved),patch.object(m.socket,'create_connection',side_effect=OSError()),patch.object(m.subprocess,'Popen',return_value=process),patch.object(m.os,'killpg') as kill:
            forward=m.SSHForward(op,'172.16.0.2')
            with self.assertRaisesRegex(RuntimeError,'exited'):forward.__enter__()
            self.assertTrue(forward.closed);self.assertGreaterEqual(kill.call_count,1);process.wait.assert_called_once()

    def test_cli_requires_pinned_inventory_and_explicit_operator(self):
        from evaluation.gke.cli import parser
        args=['bootstrap-controller-rbac']
        for name,value in vars(self.args()).items():args.extend(['--'+name.replace('_','-'),value])
        args+=['--inventory-uri','gs://evidence/control/run-1/inventory/'+'a'*64+'.json','--inventory-generation','12','--receipt','receipt.json']
        parsed=parser().parse_args(args)
        self.assertEqual(parsed.inventory_generation,'12');self.assertEqual(parsed.operator_account,'operator@example.com')

    def test_cli_unsuccessful_bootstrap_returns_nonzero(self):
        import contextlib,io
        from evaluation.gke.cli import main
        args=['bootstrap-controller-rbac']
        for name,value in vars(self.args()).items():args.extend(['--'+name.replace('_','-'),value])
        args+=['--inventory-uri','gs://evidence/control/run-1/inventory/'+'a'*64+'.json','--inventory-generation','12','--receipt','receipt.json']
        with patch('evaluation.gke.cli.dispatch',return_value={'verified':False}),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(args),2)

    def test_inventory_must_also_bind_controller_vm_uid(self):
        op=self.proof_operator();op.docs[op.args.inventory_uri][0]['resources'][1]['uid']='old-vm'
        with patch('evaluation.gke.operator_authorization.time.time',return_value=100),self.assertRaisesRegex(ValueError,'controller'):op.check()

    def test_tls_failure_still_closes_tunnel_and_removes_temporary_iap_rule(self):
        import tempfile
        from pathlib import Path
        from evaluation.tests.test_iap_recovery import IAPTests
        m=self.module();op=IAPTests().operator();args=self.args();op.args=args;op.account='controller@p.iam.gserviceaccount.com';op.creation_proof={};op.remaining=lambda:60;op.check=lambda:('172.16.0.2','Y2E=')
        original=op.call
        def call(*arguments):
            if arguments[:2]==('auth','print-access-token'):return 'private-token'
            return original(*arguments)
        op.call=call
        # Existing IAP fake instance needs the same verified controller UID.
        instance=op.instance
        op.instance=lambda:dict(instance(),id=args.uid)
        forwards=[]
        class Forward:
            port=1234;closed=False
            def __init__(self,*ignored):forwards.append(self)
            def __enter__(self):return self
            def __exit__(self,*ignored):self.closed=True
        with tempfile.TemporaryDirectory() as d:
            args.receipt=Path(d)/'receipt.json'
            r=m.bootstrap(Path(d),args,operator_factory=lambda *_:op,tunnel_factory=Forward,
                          api_factory=lambda *_,**kw:(_ for _ in ()).throw(ValueError('invalid observed cluster CA')))
            self.assertFalse(r['verified']);self.assertTrue(r['temporary_removal_confirmed']);self.assertTrue(forwards[0].closed)
            self.assertNotIn('private-token',args.receipt.read_text())
            self.assertTrue(any(c[:3]==('compute','firewall-rules','delete') for c in op.calls))

    def test_unremoved_tunnel_or_firewall_cannot_report_verified(self):
        import tempfile
        from pathlib import Path
        m=self.module();op=SimpleNamespace(check=lambda:('ip','ca'))
        with tempfile.TemporaryDirectory() as d:
            args=self.args();args.receipt=Path(d)/'receipt.json'
            for result in ({'temporary_removal_confirmed':False,'observation':{'verified':True,'tunnel_removed':True}}, {'temporary_removal_confirmed':True,'observation':{'verified':True,'tunnel_removed':False}}):
                with patch.object(m,'recover',return_value=result):
                    r=m.bootstrap(Path(d),args,operator_factory=lambda *_:op)
                    self.assertFalse(r['verified'])
                args.receipt.unlink()

    def test_credential_override_and_resolved_impersonation_are_rejected(self):
        m=self.module()
        for key in ('CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT','CLOUDSDK_AUTH_ACCESS_TOKEN','CLOUDSDK_AUTH_ACCESS_TOKEN_FILE','ACCESS_TOKEN_FILE'):
            with self.subTest(key=key),self.assertRaises(ValueError):
                m.verify_operator_configuration(SimpleNamespace(env={key:'override'},call=lambda *a:'(unset)\n'))
        op=SimpleNamespace(env={},call=lambda *a:'impersonated@example.com\n' if a[-1]=='auth/impersonate_service_account' else '(unset)\n')
        with self.assertRaisesRegex(ValueError,'override'):m.verify_operator_configuration(op)

    def test_actual_operator_identity_must_match_requested_account(self):
        m=self.module();calls=[]
        def api(*args):calls.append(args);return {'status':{'userInfo':{'username':'other@example.com'}}}
        with self.assertRaisesRegex(ValueError,'operator identity'):m.verify_operator_identity(api,'operator@example.com')
        self.assertEqual(calls[0][1],'/apis/authentication.k8s.io/v1/selfsubjectreviews')

    def test_inventory_content_hash_must_match_uri_not_only_generation(self):
        m=self.module();op=object.__new__(m.BootstrapOperator)
        def call(*args):return '{"generation":"1"}' if args[:3]==('storage','objects','describe') else '{"run_id":"changed"}\n'
        op.call=call
        with self.assertRaisesRegex(ValueError,'hash'):op._read('gs://evidence/control/run-1/inventory/'+'a'*64+'.json','1')

    def test_expired_bootstrap_budget_blocks_work_but_allows_firewall_cleanup(self):
        m=self.module();op=object.__new__(m.BootstrapOperator);op.args=self.args();op.env={};op.work_expires=10;op.cleanup_cutoff=1000;calls=[]
        def executor(args,**kwargs):calls.append((args,kwargs));return SimpleNamespace(returncode=0,stdout='[]',stderr='')
        op.executor=executor
        with patch.object(m.time,'monotonic',return_value=11),patch.object(m.time,'time',return_value=100):
            with self.assertRaises(TimeoutError):op.call('container','clusters','describe','cluster')
            self.assertEqual(op.call('compute','firewall-rules','delete','owned','--quiet'),'[]')
        self.assertEqual(len(calls),1)

    def test_private_api_shared_deadline_prevents_request_after_expiry(self):
        from unittest.mock import MagicMock
        m=self.module();api=object.__new__(m.PrivateAPI);api.endpoint='172.16.0.2';api.port=1234;api.token='secret';api.context=MagicMock();api.remaining=lambda:0
        with patch.object(m.socket,'create_connection') as connect,self.assertRaises(TimeoutError):api('POST',m.RBAC,{})
        connect.assert_not_called()
