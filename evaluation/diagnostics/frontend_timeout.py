"""Offline candidate preset for the reproduced Astronomy upstream idle-close race.

Input is the original upstream Envoy YAML template. Environment substitutions
and the image entrypoint remain intact. No function in this module applies or deploys resources.
Use the unchanged application's immutable image for Envoy --mode validate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HTTP_OPTIONS = 'envoy.extensions.upstreams.http.v3.HttpProtocolOptions'
CONFIGMAP = 'eval-frontend-upstream-timeout'
REFERENCES = {
    'envoy_protocol_contract': 'https://github.com/envoyproxy/envoy/blob/v1.39.1/api/envoy/extensions/upstreams/http/v3/http_protocol_options.proto#L23',
    'envoy_idle_default': 'https://github.com/envoyproxy/envoy/blob/v1.39.1/api/envoy/config/core/v3/protocol.proto#L312',
    'envoy_timeout_resolution': 'https://github.com/envoyproxy/envoy/blob/v1.39.1/source/common/upstream/upstream_impl.cc#L1362',
    'node_server_idle': 'https://github.com/nodejs/node/blob/v24.21.0/lib/_http_server.js#L562',
    'astronomy_bootstrap': 'https://github.com/open-telemetry/opentelemetry-demo/blob/3.1.0/src/frontend-proxy/envoy.tmpl.yaml',
    'astronomy_entrypoint': 'https://github.com/open-telemetry/opentelemetry-demo/blob/3.1.0/src/frontend-proxy/Dockerfile',
}


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


FRONTEND_OPTIONS = """      typed_extension_protocol_options:
        envoy.extensions.upstreams.http.v3.HttpProtocolOptions:
          "@type": type.googleapis.com/envoy.extensions.upstreams.http.v3.HttpProtocolOptions
          common_http_protocol_options:
            idle_timeout: 4s
          explicit_http_config:
            http_protocol_options: {}
"""


def build_preset(template):
    import re
    if not isinstance(template,str) or template.lstrip().startswith(('{','[')):
        raise ValueError('original Envoy YAML template required')
    clusters=list(re.finditer(r'^  clusters:\s*$',template,re.MULTILINE))
    names=list(re.finditer(r'^    - name: frontend[ \t]*$',template,re.MULTILINE))
    if len(clusters)!=1 or len(names)!=1 or names[0].start()<clusters[0].end():
        raise ValueError('expected exactly one frontend upstream cluster')
    name=names[0];following=re.search(r'^(?:    - name:|[A-Za-z_])',template[name.end()+1:],re.MULTILINE)
    end=name.end()+1+following.start() if following else len(template)
    block=template[name.end()+1:end]
    for key in ('typed_extension_protocol_options','http_protocol_options','protocol_selection','http2_protocol_options','common_http_protocol_options','upstream_http_protocol_options'):
        if re.search(r'^      '+key+r':',block,re.MULTILINE):
            raise ValueError('existing explicit HTTP policy requires manual review')
    patched=template[:name.end()+1]+FRONTEND_OPTIONS+template[name.end()+1:]
    return {
        'configmap': {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':CONFIGMAP,
            'namespace':'karthos-eval-astronomy','labels':{'evaluation.karthos.dev/owner':'karthos-evaluation'}},
            'data':{'envoy.tmpl.yaml':patched}},
        'helm_values':{'components':{'frontend-proxy':{'mountedConfigMaps':[{
            'name':'evaluation-frontend-timeout','mountPath':'/home/envoy/envoy.tmpl.yaml',
            'subPath':'envoy.tmpl.yaml','existingConfigMap':CONFIGMAP}]}}},
        'manifest':{'status':'candidate-not-live-validated','scope':'evaluation-owned Astronomy frontend-proxy only',
            'upstream_idle_seconds':4,'observed_node_idle_seconds':6,
            'upstream_protocol':'HTTP1; same as implicit original frontend cluster',
            'input_sha256':hashlib.sha256(template.encode()).hexdigest(),
            'candidate_sha256':hashlib.sha256(patched.encode()).hexdigest(),
            'astronomy_app':'3.1.0','chart_version':'0.42.2','envoy_version':'1.39.1','references':REFERENCES,
            'startup':'original image entrypoint/envsubst preserved; no command or image override',
            'validation':{'exact_image_config':'required','controlled_idle_packet_repeat':'required'},
            'historical_original_cause':'unknown; prospective race does not prove historical initiating cause'},
    }


def write_preset(template,output):
    preset=build_preset(template);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'envoy-source.tmpl.yaml').write_text(template)
    (output/'envoy.tmpl.yaml').write_text(preset['configmap']['data']['envoy.tmpl.yaml'])
    for name,value in [('configmap.json',preset['configmap']),('helm-values.json',preset['helm_values']),('preset-manifest.json',preset['manifest'])]:
        (output/name).write_bytes(encoded(value))
    (output/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(output.iterdir()) if p.is_file()))
    return preset


def pinned_chart_values():
    """Protocol4 values; chart owns the ConfigMap and retains image startup."""
    folder=Path(__file__).resolve().parents[1]/'presets'/'astronomy-3.1.0'
    manifest=json.loads((folder/'manifest.json').read_text())
    source=(folder/manifest['source']).read_bytes()
    if manifest['app_version']!='3.1.0' or hashlib.sha256(source).hexdigest()!=manifest['sha256']:
        raise ValueError('pinned Astronomy source template changed')
    preset=build_preset(source.decode());values=preset['helm_values']['components']['frontend-proxy']
    mount=values['mountedConfigMaps'][0]
    del mount['existingConfigMap']
    mount['data']=preset['configmap']['data']
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    write_preset(args.template.read_text(), args.output)
    print('Candidate preset:', args.output)


if __name__ == '__main__':
    main()
