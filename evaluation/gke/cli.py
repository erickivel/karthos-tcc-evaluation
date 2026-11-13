"""Prepare and operate the GKE experiment. Human confirmation happens in chat."""
from __future__ import annotations
import argparse,json,os,sys,signal
from pathlib import Path
from .models import read,sha,identifier,QUALIFICATION,REHEARSAL,THESIS,REPAIR
from .context import LiveContext


def parser():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--config',default='config/gke.example.yaml');prep.add_argument('--id',required=True);prep.add_argument('--product-snapshot');prep.add_argument('--product-lock',type=Path);prep.add_argument('--local-evidence',type=Path);prep.add_argument('--benchmark',type=Path);prep.add_argument('--schedule',choices=(THESIS,REPAIR),default=THESIS)
    prep.add_argument('--origin-report',type=Path);prep.add_argument('--origin-seal',type=Path);prep.add_argument('--origin-uri')
    prep.add_argument('--continuation-dir',type=Path);prep.add_argument('--continuation-receipt',type=Path)
    analysis=sub.add_parser('analyse');analysis.add_argument('--run-id',required=True);analysis.add_argument('--analysis-id',required=True)
    for name in ['provision','run','runtime']:
        q=sub.add_parser(name);q.add_argument('--manifest',required=True);q.add_argument('--run-id')
    for name in ['status','teardown','export']:
        q=sub.add_parser(name);q.add_argument('--run-id',required=True)
    return p


def dispatch(a,root):
    root=Path(root)
    if a.command=='analyse':
        from .analysis import analyse
        return analyse(root,a.run_id,a.analysis_id)
    if a.command=='prepare':
        from .prepare import prepare
        return {'manifest':str(prepare(root,root/a.config,a.id,product_snapshot=a.product_snapshot,schedule=a.schedule,benchmark=getattr(a,'benchmark',None),product_lock=getattr(a,'product_lock',None),local_evidence=getattr(a,'local_evidence',None),continuation_dir=getattr(a,'continuation_dir',None),continuation_receipt=getattr(a,'continuation_receipt',None),origin_report=getattr(a,'origin_report',None),origin_seal=getattr(a,'origin_seal',None),origin_uri=getattr(a,'origin_uri',None))),'cloud_started':False}
    if a.command=='status':
        from .export import redact_document
        data=Path(os.environ.get('GKE_EVAL_DATA_ROOT',str(root)));state=data/'.work/gke'/identifier(a.run_id)/'execution.json'
        return {'run_id':a.run_id,'execution':redact_document(read(state)) if state.exists() else {'phase':'not started'},'origin':'saved local control state; not a live VM/process observation'}
    if a.command=='teardown':
        from .teardown import load_cleanup_context,teardown
        ctx=load_cleanup_context(root,a.run_id)
        with ctx.controller_lock():return teardown(ctx)
    if a.command=='export':
        from .export import upload,read_only_export_context
        ctx=read_only_export_context(root,a.run_id);report=ctx.result/'run-report.json'
        if not report.exists():raise ValueError('manual export requires a finished/partial report; completed windows export automatically while active')
        return upload(ctx,ctx.result,bucket=ctx.config['gcp']['project_id']+'-karthos-eval-evidence',prefix='gke-qualification-v1/runs/'+ctx.run_id+'/exports/manual-'+sha(report)[:20])
    bootstrap=os.environ.get('GKE_EVAL_BOOTSTRAP_CONTEXT') if a.command=='runtime' else None
    run_id=read(bootstrap)['run_id'] if bootstrap else a.run_id
    ctx=LiveContext(root,Path(a.manifest),run_id=run_id)
    from .readiness import validate_launch
    validate_launch(ctx)
    with ctx.controller_lock():
        if a.command=='provision':
            from .provision import provision
            from .timing import timed
            with timed(ctx,'infrastructure-provisioning'):
                return provision(ctx)
        if a.command=='run':
            from .provision import start_controller
            return start_controller(ctx)
        from .run import run
        previous=signal.getsignal(signal.SIGTERM)
        def interrupted(signum,frame):raise KeyboardInterrupt('controller termination requested')
        signal.signal(signal.SIGTERM,interrupted)
        try:return run(ctx)
        finally:signal.signal(signal.SIGTERM,previous)


def main(argv=None):
    args=parser().parse_args(argv);root=Path(__file__).resolve().parents[2]
    try:result=dispatch(args,root)
    except (ValueError,RuntimeError,OSError,TimeoutError) as exc:
        print(json.dumps({'state':'blocked','reason':str(exc),'command':args.command}),file=sys.stderr);return 2
    print(json.dumps(result,indent=2,default=str));return 0

if __name__=='__main__':raise SystemExit(main())
