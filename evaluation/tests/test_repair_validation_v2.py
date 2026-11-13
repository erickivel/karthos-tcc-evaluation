import copy,unittest
from pathlib import Path
from evaluation.gke import models,setup
ROOT=Path(__file__).resolve().parents[2]
V2='thesis-repair-validation-v2'
class RepairV2Tests(unittest.TestCase):
    def protocol(self):return models.read(ROOT/'protocols'/V2/'protocol.yaml')
    def test_six_serial_windows_with_original_logical_ids(self):
        rows=models.build_matrix(self.protocol(),schedule=V2)
        self.assertEqual([(r['cell'],r['phase']) for r in rows],[(c,p) for c in ['boutique-mesh','astronomy-mesh','boutique-trace'] for p in ['smoke','baseline']])
        self.assertEqual([r['duration_seconds'] for r in rows],[60,300]*3)
        self.assertEqual({(r['generator'],r['rate'],r['repeat'],r['execution'],r['history_observation']) for r in rows},{('k6',2,1,'serial',False)})
        self.assertEqual([r['origin_id'] for r in rows[::2]],['repair-smoke-boutique-mesh-k6-matched','repair-smoke-astronomy-mesh-k6-matched','repair-smoke-boutique-trace-k6-matched'])
        self.assertTrue(all(r['origin_run_id']=='thesis-repair-09a2330-20261113' for r in rows))
    def test_new_active_contract_is_frozen(self):
        for key,value in [('capture_finalization_version',1),('servicegraph_store_max_items',1000),('baseline_rate',3),('generators',['locust']),('rebuild_cluster',True),('attempt_policy',{})]:
            p=self.protocol();p[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):models.build_matrix(p,schedule=V2)
    def test_explicit_v2_plan_flag_preserves_old_thesis(self):
        args=setup.plan_command('p','v','r','c',thesis=True,servicegraph_store_max_items=10000)
        self.assertIn('--servicegraph-store-max-items=10000',args)
        self.assertFalse(any('--servicegraph-store-max-items' in x for x in setup.plan_command('p','v','r','c',thesis=True)))
        with self.assertRaises(ValueError):setup.plan_command('p','v','r','c',thesis=True,servicegraph_store_max_items=0)
    def test_v2_active_settings_and_quarantine_gate(self):
        from evaluation.gke.models import validate_v2_inputs
        config={'limits':{'optional_local_download_bytes':5*1024**3},'capture_finalization_version':2,'servicegraph_store_max_items':10000,'quarantine':{'bucket':'q','prefix':'gke-quarantine-v1/runs/epoch/'},'gcp':{'foundation_reference':{'expected_outputs':{'quarantine_bucket':'q'}}}}
        manifest={'schedule':V2,'protocol_definition':self.protocol(),'config':config,'capture_finalization_version':2,'servicegraph_store_max_items':10000}
        validate_v2_inputs(manifest,'epoch')
        for key in ('capture_finalization_version','servicegraph_store_max_items','quarantine'):
            changed=copy.deepcopy(manifest);del changed['config'][key]
            with self.subTest(key=key),self.assertRaises(ValueError):validate_v2_inputs(changed,'epoch')
        with self.assertRaises(ValueError):validate_v2_inputs(manifest,'different-epoch')

class EffectiveSettingsTests(unittest.TestCase):
    def connector(self):return {'connectors':{'service_graph':{'store':{'max_items':10000,'ttl':'30s'},'metrics_flush_interval':'15s','latency_histogram_buckets':['100ms','250ms','500ms','1s','5s']}}}
    def test_semantic_duration_bounds_and_capacity(self):
        from evaluation.gke.setup import validate_connector_settings
        validate_connector_settings(self.connector(),10000)
        doc=self.connector();doc['connectors']['service_graph']['latency_histogram_buckets']=['0.1s','0.25s','500ms','1000ms','5s'];validate_connector_settings(doc,10000)
        for field,value in [('latency_histogram_buckets',['250ms','100ms','500ms','1s','5s']),('metrics_flush_interval','30s')]:
            doc=self.connector();doc['connectors']['service_graph'][field]=value
            with self.assertRaises(ValueError):validate_connector_settings(doc,10000)
        doc=self.connector();doc['connectors']['service_graph']['store']['max_items']=1000
        with self.assertRaises(ValueError):validate_connector_settings(doc,10000)
    def test_locked_running_images_require_both_spec_and_runtime_digest(self):
        from evaluation.gke.setup import validate_running_images
        lock={'backend':'registry/backend@sha256:'+'a'*64}
        rows=[{'image':lock['backend'],'image_id':'docker-pullable://'+lock['backend']}]
        validate_running_images(rows,lock)
        for key in ('image','image_id'):
            changed=copy.deepcopy(rows);changed[0][key]='registry/backend@sha256:'+'b'*64
            with self.assertRaises(ValueError):validate_running_images(changed,lock)

class FamilySummaryTests(unittest.TestCase):
    def test_unrelated_missing_metric_does_not_hide_traffic_failure(self):
        from evaluation.gke.report_summary import family_summaries
        report={'selected_accuracy':[{'name':'servicegraph.request.count','state':'fail'}],'numerical_agreement':{'state':'insufficient'},'resources':{'state':'incomplete'},'traffic':{'report':{'metric_pipeline_verification':{'checks':{'exact_request_count_api':{'state':'fail'},'exact_error_count_api':{'state':'pass'},'exact_duration_api':{'state':'pass'}}},'api_storage_parity':{'checks':{'request-count':{'state':'pass'},'error-count':{'state':'pass'},'duration':{'state':'pass'}}},'recorder_evidence':{'valid':False},'generator_contract':{'retained_generator':{'unexpected_outcomes':1}}}}}
        families=family_summaries(report)
        self.assertEqual(families['traffic_count']['state'],'fail')
        self.assertEqual(families['exact_api_storage_parity']['state'],'pass')
        self.assertEqual(families['resource_accuracy']['state'],'insufficient')
        self.assertEqual(families['application_outcomes']['state'],'fail')
        self.assertNotIn('global_pass',families)

class CredentialContextTests(unittest.TestCase):
    def test_defaults_are_isolated_and_explicit_settings_survive(self):
        import tempfile
        from evaluation.gke.cloud_identity import command_environment
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            default=command_environment(root,{})
            self.assertEqual(default['CLOUDSDK_CONFIG'],str(root/'.work/gke/gcloud'))
            self.assertEqual(default['GOOGLE_APPLICATION_CREDENTIALS'],str(root/'.work/gke/gcloud/application_default_credentials.json'))
            explicit={'CLOUDSDK_CONFIG':'/explicit/cloud','GOOGLE_APPLICATION_CREDENTIALS':'/explicit/adc.json'}
            actual=command_environment(root,explicit)
            self.assertEqual(actual['CLOUDSDK_CONFIG'],explicit['CLOUDSDK_CONFIG'])
            self.assertEqual(actual['GOOGLE_APPLICATION_CREDENTIALS'],explicit['GOOGLE_APPLICATION_CREDENTIALS'])
    def test_cleanup_command_preserves_sanitized_stderr_and_private_raw(self):
        import tempfile
        from types import SimpleNamespace
        from evaluation.gke.teardown import CleanupContext
        with tempfile.TemporaryDirectory() as directory:
            ctx=CleanupContext.__new__(CleanupContext);ctx.root=Path(directory);ctx.work=ctx.root/'private';ctx.work.mkdir();ctx.state={};ctx.run_id='epoch'
            ctx.executor=lambda *a,**k:SimpleNamespace(returncode=7,stdout='',stderr='Permission denied: storage.objects.get; access_token=SECRET')
            with self.assertRaisesRegex(RuntimeError,'storage.objects.get') as raised:ctx.command(['gcloud','storage','cat','gs://q/exact'],cleanup=True)
            self.assertNotIn('SECRET',str(raised.exception))
            files=list((ctx.work/'command-diagnostics').glob('*.stderr'))
            self.assertEqual(len(files),1);self.assertIn('SECRET',files[0].read_text());self.assertEqual(files[0].stat().st_mode&0o777,0o600)
    def test_live_cleanup_works_after_deadline(self):
        import tempfile,time
        from types import SimpleNamespace
        from evaluation.gke.context import LiveContext
        with tempfile.TemporaryDirectory() as directory:
            ctx=LiveContext.__new__(LiveContext);ctx.root=Path(directory);ctx.work=ctx.root;ctx.deadline=time.time()-30;ctx.config={'limits':{'cleanup_reserve_seconds':1800}};ctx.manifest={};ctx.state={}
            ctx.executor=lambda *a,**k:SimpleNamespace(returncode=0,stdout='',stderr='')
            ctx.command(['gcloud','storage','cat','gs://q/x'],cleanup=True)
            with self.assertRaises(TimeoutError):ctx.command(['gcloud','storage','cat','gs://q/x'])

class ControllerMetadataIdentityTests(unittest.TestCase):
    def test_bootstrapped_controller_keeps_metadata_adc_without_fake_file(self):
        from evaluation.gke.cloud_identity import command_environment
        env=command_environment('/repo',{'GKE_EVAL_BOOTSTRAP_CONTEXT':'/experiment/bootstrap-context.json','CLOUDSDK_CONFIG':'/data/gcloud'})
        self.assertNotIn('GOOGLE_APPLICATION_CREDENTIALS',env)
        env['GOOGLE_APPLICATION_CREDENTIALS']='/explicit/adc.json'
        self.assertEqual(command_environment('/repo',env)['GOOGLE_APPLICATION_CREDENTIALS'],'/explicit/adc.json')

class EffectiveConfigurationIntegrationTests(unittest.TestCase):
    def test_generated_and_desired_match_while_mounted_proof_stays_pending(self):
        import tempfile,yaml
        from types import SimpleNamespace
        from evaluation.gke.setup import verify_effective_settings
        image='registry/collector@sha256:'+'a'*64
        config=EffectiveSettingsTests().connector();text=yaml.safe_dump(config)
        pod={'metadata':{'name':'gateway','uid':'pod-uid','labels':{'app.kubernetes.io/component':'gateway'}},
             'spec':{'containers':[{'name':'gateway','image':image,'volumeMounts':[{'name':'config','mountPath':'/conf'}]}],
                     'volumes':[{'name':'config','configMap':{'name':'gateway-config'}}]},
             'status':{'containerStatuses':[{'name':'gateway','image':image,'imageID':'docker-pullable://'+image}]}}
        cm={'metadata':{'uid':'config-uid'},'data':{'config.yaml':text}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);values=path/'values.yaml';values.write_text(yaml.safe_dump({'gateway':{'collectorConfig':text}}))
            ctx=SimpleNamespace(config={'capture_finalization_version':2,'servicegraph_store_max_items':10000,'infrastructure':{}},
                manifest={'capture_finalization_version':2,'servicegraph_store_max_items':10000,'image_lock':{'collector':image}},
                json=lambda *a:({'items':[pod]} if a[1]=='pods' else cm),kube=lambda *a,**kw:self.fail('cannot use a collector cat or a separate probe as mounted proof'))
            receipt=verify_effective_settings(ctx,path,values,trace=True)
            self.assertEqual(receipt['mounted_file_proof']['state'],'pending')
            self.assertEqual(receipt['desired_configmaps'][0]['pod_uid'],'pod-uid')
            self.assertEqual(receipt['generated']['store_max_items'],10000)
            changed=copy.deepcopy(config);changed['connectors']['service_graph']['store']['max_items']=1000
            cm['data']['config.yaml']=yaml.safe_dump(changed)
            with self.assertRaisesRegex(ValueError,'differs from generated'):verify_effective_settings(ctx,path,values,trace=True)

class SourceBundleClosureTests(unittest.TestCase):
    def test_every_active_implementation_path_is_publishable(self):
        from evaluation.gke.bundle import _safe_name
        from evaluation.gke.models import implementation_hashes
        for name in implementation_hashes(ROOT):
            with self.subTest(name=name):_safe_name(name)


class GKEImageIdentityTests(unittest.TestCase):
    def pod(self):
        image='docker.io/otel/opentelemetry-collector-contrib@sha256:'+'a'*64
        return image,{'metadata':{'name':'agent','uid':'uid'},'spec':{'nodeName':'worker','containers':[{'name':'agent','image':image}]},'status':{'containerStatuses':[{'name':'agent','image':'sha256:'+'b'*64,'imageID':image}]}}
    def test_gke_local_status_image_is_not_the_declared_image(self):
        image,pod=self.pod()
        rows=setup.running_image_inventory({'items':[pod]})
        self.assertEqual(rows[0]['image'],image)
        self.assertEqual(rows[0]['reported_image'],'sha256:'+'b'*64)
        self.assertEqual(rows[0]['pod_uid'],'uid')
        setup.validate_running_images(rows,{'collector':image})
    def test_missing_or_ambiguous_runtime_status_is_rejected(self):
        for statuses in ([],[{'name':'agent','imageID':'x'}]*2):
            image,pod=self.pod();pod['status']['containerStatuses']=statuses
            with self.subTest(statuses=statuses),self.assertRaises(ValueError):
                setup.validate_running_images(setup.running_image_inventory({'items':[pod]}),{'collector':image})
    def test_init_containers_require_their_own_matching_status(self):
        image,pod=self.pod();pod['spec']['initContainers']=[{'name':'init','image':image,'restartPolicy':'Always'}]
        pod['status']['initContainerStatuses']=[{'name':'init','image':'local','imageID':image}]
        rows=setup.running_image_inventory({'items':[pod]})
        self.assertEqual(len(rows),2);setup.validate_running_images(rows,{'collector':image})
        rows[1]['image_id']='registry/wrong@sha256:'+'c'*64
        with self.assertRaises(ValueError):setup.validate_running_images(rows,{'collector':image})
    def test_image_failure_inputs_are_saved_before_exception(self):
        import tempfile,yaml,json
        from types import SimpleNamespace
        image,pod=self.pod();pod['status']['containerStatuses'][0]['imageID']='wrong@sha256:'+'c'*64
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);values=path/'values.yaml';values.write_text('{}')
            ctx=SimpleNamespace(config={'capture_finalization_version':2,'servicegraph_store_max_items':10000,'infrastructure':{}},manifest={'capture_finalization_version':2,'servicegraph_store_max_items':10000,'image_lock':{'collector':image}},json=lambda *a:{'items':[pod]})
            with self.assertRaises(ValueError):setup.verify_effective_settings(ctx,path,values,trace=False)
            proof=json.loads((path/'running-image-verification.json').read_text())
            self.assertEqual(proof['state'],'failed');self.assertEqual(proof['rows'][0]['image'],image)

    def test_effective_check_accepts_matching_spec_and_runtime_with_local_status_id(self):
        import tempfile
        from types import SimpleNamespace
        image,pod=self.pod()
        with tempfile.TemporaryDirectory() as d:
            path=Path(d);values=path/'values.yaml';values.write_text('{}')
            ctx=SimpleNamespace(config={'capture_finalization_version':2,'servicegraph_store_max_items':10000,'infrastructure':{}},manifest={'capture_finalization_version':2,'servicegraph_store_max_items':10000,'image_lock':{'collector':image}},json=lambda *a:{'items':[pod]})
            try:setup.verify_effective_settings(ctx,path,values,trace=False)
            except ValueError as error:self.fail('correctly pinned GKE image rejected: '+str(error))
