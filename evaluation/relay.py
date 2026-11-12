"""Evaluation-owned Boutique OTLP relay with independent source log output."""
from __future__ import annotations
import json
from .manifests import OWNER_LABEL, OWNER_VALUE, collector_self_metrics


def boutique_relay(namespace: str, image: str, *, self_metrics: bool = False) -> list[dict]:
    config = '''receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
processors:
  batch: {}
exporters:
  otlp/karthos:
    endpoint: ${env:KARTHOS_AGENT_HOST}:4317
    tls:
      insecure: true
  debug:
    verbosity: detailed
    use_internal_logger: false
    sampling_thereafter: 1
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/karthos, debug]
    metrics:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlp/karthos]
'''
    if self_metrics:
        config=config.replace('service:\n', 'service:\n  telemetry: '+json.dumps({'metrics':collector_self_metrics()})+'\n')
    labels = {OWNER_LABEL: OWNER_VALUE, 'app': 'opentelemetrycollector'}
    return [
        {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'eval-otlp-relay','namespace':namespace,'labels':labels},'data':{'config.yaml':config}},
        {'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'eval-otlp-relay','namespace':namespace,'labels':labels},
         'spec':{'replicas':1,'selector':{'matchLabels':{'app':'opentelemetrycollector'}},'template':{
             'metadata':{'labels':labels},'spec':{'containers':[{'name':'collector','image':image,
                 'args':['--config=/conf/config.yaml'],
                 'env':[{'name':'KARTHOS_AGENT_HOST','valueFrom':{'fieldRef':{'fieldPath':'status.hostIP'}}}],
                 'ports':[{'containerPort':4317,'name':'grpc'},{'containerPort':4318,'name':'http'}],
                 'volumeMounts':[{'name':'config','mountPath':'/conf','readOnly':True}],
                 'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'500m','memory':'512Mi'}}}],
                 'volumes':[{'name':'config','configMap':{'name':'eval-otlp-relay'}}]}}}},
        {'apiVersion':'v1','kind':'Service','metadata':{'name':'opentelemetrycollector','namespace':namespace,'labels':labels},
         'spec':{'selector':{'app':'opentelemetrycollector'},'ports':[{'name':'grpc','port':4317,'targetPort':4317},{'name':'http','port':4318,'targetPort':4318}]}}
    ]
