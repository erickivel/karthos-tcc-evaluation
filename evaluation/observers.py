"""Evaluation-owned HTTP/gRPC boundary records, independent of OTel and Istio.

The observer adds a proxy boundary to each selected application Pod. Its timing
includes proxy/upstream transport, so it is not silently equated with span time.
"""
from __future__ import annotations

import copy


def observer_config(boundary: str, upstream_port: int, *, grpc: bool = False) -> str:
    forwarding = (f'grpc_pass grpc://127.0.0.1:{upstream_port}; grpc_next_upstream off;'
                  if grpc else f'proxy_pass http://127.0.0.1:{upstream_port}; '
                  'proxy_http_version 1.1; proxy_set_header Connection ""; '
                  'proxy_set_header Host $http_host; proxy_next_upstream off;')
    return '''worker_processes 1;
error_log /dev/stderr warn;
pid /tmp/nginx.pid;
events { worker_connections 1024; }
http {
  client_body_temp_path /tmp/client_temp;
  proxy_temp_path /tmp/proxy_temp;
  fastcgi_temp_path /tmp/fastcgi_temp;
  uwsgi_temp_path /tmp/uwsgi_temp;
  scgi_temp_path /tmp/scgi_temp;
  map $upstream_trailer_grpc_status $eval_grpc_status {
    default $upstream_trailer_grpc_status;
    "" $upstream_http_grpc_status;
  }
  log_format evidence escape=json '{"observed_at":$msec,"boundary":"BOUNDARY",'
    '"request_id":"$http_x_evaluation_request_id","run_id":"$http_x_evaluation_run",'
    '"uri":"$uri","method":"$request_method","status":$status,"user_agent":"$http_user_agent",'
    '"request_time_seconds":"$request_time","upstream_time_seconds":"$upstream_response_time",'
    '"grpc_status":"$eval_grpc_status","traceparent":"$http_traceparent",'
    '"remote_addr":"$remote_addr","bytes":$body_bytes_sent}';
  access_log /dev/stdout evidence;
  server {
    listen 18080;
    HTTP2
    location / { FORWARD }
  }
}
'''.replace('BOUNDARY', boundary).replace('HTTP2', 'http2 on;' if grpc else '').replace('FORWARD', forwarding)


def observer_patch(boundary: str, image: str) -> dict:
    return {'spec': {'template': {'spec': {
        'containers': [{'name': 'eval-observer', 'image': image,
                        'command': ['nginx', '-c', '/eval/nginx.conf', '-g', 'daemon off;'],
                        'ports': [{'name': 'eval-observe', 'containerPort': 18080}],
                        'resources': {'requests': {'cpu': '25m', 'memory': '32Mi'},
                                      'limits': {'cpu': '250m', 'memory': '128Mi'}},
                        'volumeMounts': [{'name': 'eval-observer', 'mountPath': '/eval', 'readOnly': True}]}],
        'volumes': [{'name': 'eval-observer', 'configMap': {'name': 'eval-observer-' + boundary}}],
    }}}}


def service_patch(service: dict, *, grpc: bool = False) -> dict:
    ports = copy.deepcopy(service['spec']['ports'])
    if len(ports) != 1:
        raise ValueError('observer requires one explicit application Service port')
    ports[0]['targetPort'] = 18080
    if grpc:
        ports[0].update(name='grpc-evaluation', appProtocol='grpc')
    return {'spec': {'ports': ports}}
