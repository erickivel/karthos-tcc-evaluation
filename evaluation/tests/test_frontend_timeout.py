import importlib,json,tempfile,unittest
from pathlib import Path


def template():
    return '''static_resources:
  listeners:
    - address: {socket_address: {address: "${ENVOY_ADDR}", port_value: ${ENVOY_PORT}}}
  clusters:
    - name: frontend
      type: STRICT_DNS
      lb_policy: ROUND_ROBIN
      load_assignment:
        cluster_name: frontend
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: {address: "${FRONTEND_HOST}", port_value: ${FRONTEND_PORT}}
    - name: other
      type: STRICT_DNS
admin: {address: {socket_address: {address: "0.0.0.0", port_value: ${ENVOY_ADMIN_PORT}}}}
'''


class FrontendTimeoutTests(unittest.TestCase):
    def module(self):return importlib.import_module('evaluation.diagnostics.frontend_timeout')

    def test_only_frontend_block_changes_and_environment_tokens_survive(self):
        m=self.module();source=template();preset=m.build_preset(source)
        patched=preset['configmap']['data']['envoy.tmpl.yaml']
        self.assertEqual(patched.replace(m.FRONTEND_OPTIONS,''),source)
        self.assertIn('idle_timeout: 4s',patched)
        self.assertIn('explicit_http_config:',patched)
        self.assertIn('http_protocol_options: {}',patched)
        self.assertEqual(patched.count('${'),source.count('${'))
        self.assertNotIn('retry',patched)
        self.assertEqual(preset['manifest']['status'],'candidate-not-live-validated')

    def test_mount_preserves_original_entrypoint_and_chart_surface(self):
        m=self.module();p=m.build_preset(template())
        values=p['helm_values']['components']['frontend-proxy']
        self.assertNotIn('command',values)
        self.assertEqual(values['mountedConfigMaps'],[{'name':'evaluation-frontend-timeout','mountPath':'/home/envoy/envoy.tmpl.yaml','subPath':'envoy.tmpl.yaml','existingConfigMap':'eval-frontend-upstream-timeout'}])
        self.assertNotIn('imageOverride',values)
        self.assertEqual(p['configmap']['metadata']['labels']['evaluation.karthos.dev/owner'],'karthos-evaluation')

    def test_reject_ambiguous_missing_or_conflicting_frontend_policy(self):
        m=self.module()
        for text in [template().replace('    - name: other','    - name: frontend'),
                     template().replace('    - name: frontend','    - name: gone'),
                     template().replace('      type: STRICT_DNS','      typed_extension_protocol_options: {}',1),
                     json.dumps({'configs':[]}),
                     template().replace('      type: STRICT_DNS','      http2_protocol_options: {}',1)]:
            with self.assertRaises(ValueError):m.build_preset(text)

    def test_refuses_existing_legacy_http1_or_protocol_selection(self):
        m=self.module()
        for policy in ('http_protocol_options: {allow_absolute_url: true}',
                       'protocol_selection: USE_DOWNSTREAM_PROTOCOL'):
            with self.assertRaises(ValueError):
                m.build_preset(template().replace('      type: STRICT_DNS','      '+policy,1))

    def test_pinned_chart_values_embed_template_and_preserve_startup(self):
        m=self.module();values=m.pinned_chart_values()
        self.assertNotIn('command',values)
        mount=values['mountedConfigMaps'][0]
        self.assertNotIn('existingConfigMap',mount)
        self.assertIn('idle_timeout: 4s',mount['data']['envoy.tmpl.yaml'])
        self.assertEqual(mount['subPath'],'envoy.tmpl.yaml')

    def test_write_is_new_directory_only_and_hashes_exact_source(self):
        import hashlib
        m=self.module()
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'preset';m.write_preset(template(),out)
            manifest=json.loads((out/'preset-manifest.json').read_text())
            self.assertEqual(manifest['input_sha256'],hashlib.sha256(template().encode()).hexdigest())
            self.assertEqual((out/'envoy-source.tmpl.yaml').read_text(),template())
            self.assertTrue((out/'envoy.tmpl.yaml').exists())
            self.assertTrue((out/'helm-values.json').exists())
            with self.assertRaises(FileExistsError):m.write_preset(template(),out)

if __name__=='__main__':unittest.main()
