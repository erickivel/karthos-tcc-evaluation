import unittest

from evaluation.protocol import cells_for_repeat, source_id, safe_namespace, selected_source


class ProtocolTests(unittest.TestCase):
    def test_three_repeats_have_balanced_source_order_and_twelve_cells(self):
        repeats = [cells_for_repeat(index) for index in range(1, 4)]
        self.assertEqual(sum(len(cells) for cells in repeats), 12)
        self.assertEqual([cell.source for cell in repeats[0]], ["trace", "trace", "mesh", "mesh"])
        self.assertEqual([cell.source for cell in repeats[1]], ["mesh", "mesh", "trace", "trace"])
        self.assertEqual([cell.source for cell in repeats[2]], ["trace", "trace", "mesh", "mesh"])
        self.assertEqual({(cell.demo, cell.source) for cell in repeats[0]}, {
            ("boutique", "trace"), ("boutique", "mesh"),
            ("astronomy", "trace"), ("astronomy", "mesh"),
        })

    def test_source_mapping(self):
        self.assertEqual(source_id("trace"), "traces.servicegraph")
        self.assertEqual(source_id("mesh"), "mesh.istio")

    def test_only_evaluation_namespace_is_safe_to_remove(self):
        self.assertTrue(safe_namespace("karthos-eval-boutique"))
        self.assertFalse(safe_namespace("karthos-system"))
        self.assertFalse(safe_namespace("default"))
        self.assertFalse(safe_namespace("karthos-eval-boutique-extra"))

    def test_selected_source_reads_resolved_plan(self):
        plan = {"metric_assignments": {"servicegraph.request.count": "mesh.istio"}}
        self.assertEqual(selected_source(plan), "mesh.istio")


if __name__ == "__main__":
    unittest.main()

class LedgerDispatchTests(unittest.TestCase):
    def test_k6_warmup_uses_k6_evidence_format(self):
        from evaluation.runner import evidence_format
        self.assertEqual(evidence_format('warmup'), 'K6')
        self.assertEqual(evidence_format('k6'), 'K6')
        self.assertEqual(evidence_format('locust'), 'LOCUST')

class ImageLockTests(unittest.TestCase):
    def test_image_ids_are_extracted_from_running_pods(self):
        from evaluation.runner import image_ids
        pods={'items':[{'metadata':{'name':'p'},'status':{'containerStatuses':[{'name':'app','image':'example:v1','imageID':'docker-pullable://example@sha256:abc'}]}}]}
        self.assertEqual(image_ids(pods),[{'pod':'p','container':'app','image':'example:v1','image_id':'docker-pullable://example@sha256:abc'}])

    def test_restartable_istio_init_container_is_detected_and_locked(self):
        from evaluation.runner import has_istio_proxy, image_ids
        pod={'metadata':{'name':'frontend-abc'},
             'spec':{'containers':[{'name':'server'}],
                     'initContainers':[{'name':'istio-proxy','restartPolicy':'Always'}]},
             'status':{'initContainerStatuses':[{'name':'istio-proxy','image':'istio/proxyv2:1.28.2',
                                                 'imageID':'istio/proxyv2@sha256:abc'}]}}
        self.assertTrue(has_istio_proxy(pod))
        self.assertEqual(image_ids({'items':[pod]})[0]['container'],'istio-proxy')


class LocustManifestTests(unittest.TestCase):
    def test_astronomy_agent_port_is_numeric_and_exporters_are_disabled(self):
        from evaluation.manifests import locust_job
        job=locust_job('astronomy','realism','karthos-eval-astronomy','demo:tag','http://frontend-proxy:8080')
        env={item['name']:item['value'] for item in job['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual(env['AGENT_PORT'],'8010')
        self.assertEqual(env['OTEL_LOGS_EXPORTER'],'none')
