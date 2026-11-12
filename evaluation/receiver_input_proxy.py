"""Evaluation-only transparent kubelet input recorder; never prints credentials.

Every response preserves exact bytes (base64), parsed JSON for analysis,
request/response times, pod UID and node. It proxies only the original configured
HTTPS endpoint using the collector service-account token and the deployed
insecure_skip_verify contract. Disk overflow is explicit, never rotated away.
"""
from __future__ import annotations
import base64
import http.server
import json
import os
import re
import ssl
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

LOCK=threading.Lock()
MAX_BYTES=96*1024*1024
ROOT=Path('/diag')

def endpoint():
    value=os.environ['CAPTURE_UPSTREAM']
    value=re.sub(r'\$\{env:([^}]+)\}',lambda match:os.environ[match[1]],value)
    if not value.startswith('https://'):raise ValueError('original kubelet endpoint must be HTTPS')
    return value.rstrip('/')

def record(row):
    data=(json.dumps(row,separators=(',',':'))+'\n').encode()
    with LOCK:
        file=ROOT/'receiver-input.ndjson'
        if file.exists() and file.stat().st_size+len(data)>MAX_BYTES:
            # A bounded sentinel survives even after recording has stopped.
            sentinel=ROOT/'receiver-errors.ndjson'
            if not sentinel.exists():sentinel.write_text(json.dumps({'error':'capture disk limit reached','at_ns':time.time_ns()})+'\n')
            return False
        if len(data)>MAX_BYTES:
            (ROOT/'receiver-errors.ndjson').write_text(json.dumps({'error':'single receiver input exceeds capture bound','at_ns':time.time_ns()})+'\n');return False
        with file.open('ab') as handle:handle.write(data);handle.flush()
    return True

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        row={'requested_at_ns':time.time_ns(),'path':self.path,'collector_uid':os.environ.get('CAPTURE_UID'),'pod':os.environ.get('CAPTURE_POD'),'node_name':os.environ.get('CAPTURE_NODE'),'owner':os.environ.get('CAPTURE_OWNER')}
        code=502;body=b'';content='application/json'
        try:
            token=Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
            request=urllib.request.Request(endpoint()+self.path,headers={'Authorization':'Bearer '+token})
            try:response=urllib.request.urlopen(request,context=ssl._create_unverified_context(),timeout=20)
            except urllib.error.HTTPError as exc:response=exc
            with response:
                body=response.read();code=response.status;content=response.headers.get('Content-Type','application/json')
            row.update(status=code,body_base64=base64.b64encode(body).decode(),error=None)
            try:row['body']=json.loads(body)
            except (ValueError,UnicodeError):row.update(body=None,error='non-JSON kubelet response')
        except Exception as exc:
            # Exceptions can contain URL but never token/request headers.
            row.update(status=code,error=type(exc).__name__)
        row['received_at_ns']=time.time_ns()
        record(row)
        self.send_response(code);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

if __name__=='__main__':
    endpoint() # Validate environment before accepting requests.
    http.server.ThreadingHTTPServer(('127.0.0.1',19050),Handler).serve_forever()
