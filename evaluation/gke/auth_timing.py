"""Credential-plugin timing without recording credential input or output."""
import argparse,json,os,signal,subprocess,time
from pathlib import Path

def event(path,kind,**fields):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 data=(json.dumps(dict(event=kind,at_ns=time.time_ns(),pid=os.getpid(),parent_pid=os.getppid(),**fields))+'\n').encode()
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
 try:os.write(fd,data)
 finally:os.close(fd)

def run(command,path):
 parent=os.getppid();start=time.monotonic();event(path,'auth-start')
 try:process=subprocess.Popen(command,start_new_session=True) # credential stdout passes directly to kubectl
 except OSError as exc:
  event(path,'auth-spawn-failed',error_type=type(exc).__name__,errno=exc.errno,elapsed_seconds=time.monotonic()-start)
  return 1
 event(path,'auth-spawned',child_pid=process.pid)
 try:
  while process.poll() is None:
   if os.getppid()!=parent:
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=1)
    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
    event(path,'auth-cancelled',reason='parent-exited',elapsed_seconds=time.monotonic()-start)
    return 1
   time.sleep(.02)
  event(path,'auth-finished',exit_code=process.returncode,elapsed_seconds=time.monotonic()-start)
  return process.returncode
 finally:
  if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait()

def install(kubeconfig,output):
 import yaml,sys
 path=Path(kubeconfig);doc=yaml.safe_load(path.read_text());changed=False
 for user in doc.get('users',[]):
  spec=user.get('user',{}).get('exec')
  if not spec:continue
  if spec.get('command')==sys.executable and str(Path(__file__).resolve()) in spec.get('args',[]):continue
  if Path(spec.get('command','')).name!='gke-gcloud-auth-plugin':raise ValueError('unreviewed credential exec plugin')
  spec['args']=[str(Path(__file__).resolve()),'--output',str(Path(output).resolve()),'--',spec['command'],*spec.get('args',[])]
  spec['command']=sys.executable;changed=True
 if changed:path.write_text(yaml.safe_dump(doc,sort_keys=False));path.chmod(0o600)
 return changed

if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('command',nargs=argparse.REMAINDER)
 args=parser.parse_args();command=args.command[1:] if args.command[:1]==['--'] else args.command
 if not command:parser.error('credential command required')
 raise SystemExit(run(command,args.output))
