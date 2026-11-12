import unittest
from evaluation.manifests import demo_values, k6_job, locust_job
from evaluation import manifests


class ManifestTests(unittest.TestCase):
    def test_protocol4_astronomy_timeout_does_not_change_legacy_protocols(self):
        current=demo_values('astronomy',protocol_version=4)['components']['frontend-proxy']
        self.assertIn('idle_timeout: 4s',current['mountedConfigMaps'][0]['data']['envoy.tmpl.yaml'])
        self.assertNotIn('frontend-proxy',demo_values('astronomy',protocol_version=3)['components'])

    def test_native_locust_duration_metadata_matches_cli_runtime(self):
        job=locust_job('boutique','run','ns','image','http://unused',duration_seconds=3,artifact_mode=True)
        container=job['spec']['template']['spec']['containers'][0]
        env={row['name']:row['value'] for row in container['env']}
        self.assertEqual(env.get('DURATION'),'3s')
        self.assertIn('--run-time 3s',container['args'][0])

    def test_opt_in_artifacts_publish_files_without_stdout_dumps(self):
        jobs=[k6_job('boutique','trace','run','ns','k6','http://frontend',artifact_mode=True,recovery_seconds=1800),
              manifests.matched_locust_job('boutique','trace','run','ns','locust','http://frontend',artifact_mode=True,recovery_seconds=1800),
              locust_job('boutique','run','ns','locust','http://frontend',artifact_mode=True,recovery_seconds=1800)]
        for job in jobs:
            container=job['spec']['template']['spec']['containers'][0]
            env={row['name']:row['value'] for row in container['env']}
            self.assertEqual(env['RUN_ID'],'run')
            self.assertEqual(env['EVAL_ARTIFACT_MODE'],'1')
            self.assertEqual(env['EVAL_RECOVERY_SECONDS'],'1800')
            self.assertEqual(env['EVAL_WORKLOAD_MAX_SECONDS'],'240')
            self.assertEqual(env['EVAL_ARTIFACT_DIR'],'/tmp/eval-artifacts')
            command=container['args'][0]
            self.assertIn('SHA256SUMS',command)
            self.assertIn('READY',command)
            self.assertIn('ACK',command)
            self.assertNotIn('__EVAL_K6_BEGIN__',command)
            self.assertNotIn('__EVAL_LOCUST_stats_BEGIN__',command)
            self.assertNotIn('base64',command)

    def test_demo_generators_are_disabled_during_k6(self):
        self.assertEqual(demo_values("boutique")["loadGenerator"]["create"], False)
        self.assertEqual(demo_values("astronomy")["components"]["load-generator"]["enabled"], False)

    def test_astronomy_collector_does_not_claim_karthos_otlp_host_ports(self):
        collector=demo_values("astronomy")["opentelemetry-collector"]
        self.assertEqual(collector["ports"]["otlp"]["hostPort"],0)
        self.assertEqual(collector["ports"]["otlp-http"]["hostPort"],0)
        self.assertEqual(collector["config"]["service"]["telemetry"]["metrics"]["readers"],[])

    def test_protocol4_source_selfmetrics_are_explicit_and_local(self):
        metrics=demo_values('astronomy',protocol_version=4)['opentelemetry-collector']['config']['service']['telemetry']['metrics']
        self.assertEqual(metrics['level'],'detailed')
        prometheus=metrics['readers'][0]['pull']['exporter']['prometheus']
        self.assertEqual(prometheus['host'],'127.0.0.1')
        self.assertEqual(prometheus['port'],8888)
        self.assertTrue(prometheus['without_type_suffix'])
        from evaluation.relay import boutique_relay
        config=boutique_relay('ns','image',self_metrics=True)[0]['data']['config.yaml']
        self.assertIn('"level": "detailed"',config)
        self.assertIn('"port": 8888',config)
        self.assertNotIn('telemetry:',boutique_relay('ns','image')[0]['data']['config.yaml'])

    def test_k6_job_is_bounded_and_named_for_cell(self):
        job = k6_job("boutique", "trace", "run-1", "karthos-eval-boutique", "grafana/k6:1.8.2", "frontend:80")
        pod = job["spec"]["template"]
        self.assertEqual(job["metadata"]["name"], "eval-k6-run-1")
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(pod["metadata"]["labels"]["evaluation.karthos.dev/owner"], "karthos-evaluation")
        self.assertIn("run.js", pod["spec"]["containers"][0]["args"][-1])

    def test_matched_generators_share_long_window_and_explicit_load_model(self):
        k6 = k6_job("boutique", "trace", "r", "ns", "k6", "http://frontend", duration_seconds=300, rate=2, workload="checkout-heavy")
        locust = manifests.matched_locust_job("boutique", "trace", "r", "ns", "demo-load-generator", "http://frontend", duration_seconds=300, rate=2, workload="checkout-heavy")
        for job in (k6, locust):
            container = job['spec']['template']['spec']['containers'][0]
            env = {v['name']:v['value'] for v in container['env']}
            self.assertEqual(env['WORKLOAD'], 'checkout-heavy')
            self.assertEqual(env['DURATION'], '300s')
            self.assertEqual(env['RATE'], '2')
            self.assertEqual(env['RUN_ID'], 'r')
            self.assertTrue(container['volumeMounts'])
        command=locust['spec']['template']['spec']['containers'][0]['args'][0]
        self.assertIn('-f /scripts/matched_locust.py', command)
        self.assertIn('--users 10', command)
        self.assertIn('--spawn-rate 10', command)
        self.assertNotIn('--processes', command)

    def test_native_and_matched_locust_export_version_csv_and_html(self):
        for job in [locust_job("boutique", "r", "ns", "demo", "http://frontend"), manifests.matched_locust_job("boutique", "trace", "r", "ns", "demo", "http://frontend")]:
            command=job['spec']['template']['spec']['containers'][0]['args'][0]
            self.assertIn('locust --version', command)
            self.assertIn('--html=/tmp/locust.html', command)
            self.assertIn('__EVAL_LOCUST_html_BASE64_BEGIN__', command)
            self.assertIn('__EVAL_LOCUST_stats_BEGIN__', command)

    def test_locust_job_is_separate_and_has_fixed_runtime(self):
        job = locust_job("astronomy", "run-1", "karthos-eval-astronomy", "ghcr.io/open-telemetry/demo:3.1.0-load-generator", "http://frontend-proxy:8080")
        command = " ".join(job["spec"]["template"]["spec"]["containers"][0]["args"])
        self.assertIn("--headless", command)
        self.assertIn("--run-time", command)
        self.assertIn("--csv", command)


if __name__ == "__main__":
    unittest.main()
