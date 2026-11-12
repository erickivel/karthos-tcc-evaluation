import http.server,urllib.request,ssl,json,time,threading,os
lock=threading.Lock()
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  started=time.time_ns()
  try:
   token=open('/var/run/secrets/kubernetes.io/serviceaccount/token').read().strip()
   req=urllib.request.Request('https://'+os.environ['NODE_IP']+':10250'+self.path,headers={'Authorization':'Bearer '+token})
   with urllib.request.urlopen(req,context=ssl._create_unverified_context(),timeout=20) as response:
    body=response.read();code=response.status;content=response.headers.get('Content-Type','application/json')
   record={'requested_at_ns':started,'received_at_ns':time.time_ns(),'path':self.path,'status':code,'body':json.loads(body)}
   with lock,open('/diag/receiver-input.ndjson','a') as f:f.write(json.dumps(record)+'\n')
   self.send_response(code);self.send_header('Content-Type',content);self.end_headers();self.wfile.write(body)
  except Exception as exc:
   with lock,open('/diag/receiver-errors.ndjson','a') as f:f.write(json.dumps({'at_ns':time.time_ns(),'path':self.path,'error':str(exc)})+'\n')
   self.send_error(502,str(exc))
 def log_message(self,*args):pass
http.server.ThreadingHTTPServer(('127.0.0.1',19050),Handler).serve_forever()
