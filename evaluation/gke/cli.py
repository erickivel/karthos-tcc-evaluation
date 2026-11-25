"""Prepare and operate the GKE experiment. Human confirmation happens before cloud work."""
from __future__ import annotations
import argparse,json,os,sys,signal
from pathlib import Path
from .models import read,sha,identifier,QUALIFICATION,REHEARSAL,THESIS,REPAIR,REPAIR_V2,COMPLETION,COMPLETION_V2
from .context import LiveContext


def parser():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    build=sub.add_parser('build',help='Build pinned evaluation artifacts from an exact commit; never provisions cloud resources')
    build.add_argument('--revision',required=True);build.add_argument('--destination',required=True,type=Path)
    build.add_argument('--repository',type=Path);build.add_argument('--toolchain',type=Path)
    build.add_argument('--product-repository',help='Authorized source URL or local committed checkout; pinned product SHA and hash still required')
    build.add_argument('--image-prefix',default='karthos-eval');build.add_argument('--build-images',action='store_true')
    prep=sub.add_parser('prepare');prep.add_argument('--config',default='config/gke.completion-v2.example.yaml');prep.add_argument('--id',required=True);prep.add_argument('--product-snapshot');prep.add_argument('--product-lock',type=Path);prep.add_argument('--local-evidence',type=Path);prep.add_argument('--benchmark',type=Path);prep.add_argument('--schedule',choices=(THESIS,REPAIR,REPAIR_V2,COMPLETION,COMPLETION_V2),default=COMPLETION_V2)
    prep.add_argument('--matrix-profile',choices=('astronomy-recovery-v1','astronomy-istio-repair-v1','failed-istio-baselines-v1','astronomy-istio-baseline-v1','boutique-complement-v1','thesis-robustness-full-v1'))
    prep.add_argument('--origin-report',type=Path);prep.add_argument('--origin-seal',type=Path);prep.add_argument('--origin-uri')
    prep.add_argument('--continuation-dir',type=Path);prep.add_argument('--continuation-receipt',type=Path)
    comparison=sub.add_parser('compare-deployments');comparison.add_argument('--left',type=Path,required=True);comparison.add_argument('--right',type=Path,required=True);comparison.add_argument('--left-sha256',required=True);comparison.add_argument('--right-sha256',required=True)
    analysis=sub.add_parser('analyse');analysis.add_argument('--run-id',required=True);analysis.add_argument('--analysis-id',required=True)
    for name in ['provision','run','runtime']:
        q=sub.add_parser(name);q.add_argument('--manifest',required=True);q.add_argument('--run-id')
    for name in ['status','teardown','export']:
        q=sub.add_parser(name);q.add_argument('--run-id',required=True)
    download=sub.add_parser('download');download.add_argument('--manifest',required=True);download.add_argument('--run-id',required=True);download.add_argument('--object',dest='object_uri',required=True);download.add_argument('--destination',type=Path,required=True)
    for name in ('monitor','recover-iap'):
        mon=sub.add_parser(name,help='Observe controller; recover-iap temporarily opens narrowly scoped IAP SSH ingress')
        for flag in ('project','zone','controller','owner-id','run-id','uid'):
            mon.add_argument('--'+flag,required=True)
        mon.add_argument('--interval',type=int,default=900)
        if name=='monitor':mon.add_argument('--watch',action='store_true')
        else:mon.add_argument('--receipt',type=Path,required=True)
    bootstrap=sub.add_parser('bootstrap-controller-rbac',help='Grant controller access only inside a UID-verified owned evaluation cluster')
    for flag in ('project','zone','controller','owner-id','run-id','uid','cluster','cluster-uid','operator-account','inventory-uri','inventory-generation'):
        bootstrap.add_argument('--'+flag,required=True)
    bootstrap.add_argument('--receipt',type=Path,required=True)
    return p


def dispatch(a,root):
    root=Path(root)
    if a.command=='bootstrap-controller-rbac':
        from .operator_authorization import bootstrap
        return bootstrap(root,a)
    if a.command=='recover-iap':
        from .operator_monitor import Operator,observe
        from .iap_recovery import recover
        return recover(Operator(root,a),observe,a.receipt)
    if a.command=='monitor':
        from .operator_monitor import monitor
        return monitor(root,a)
    if a.command=='build':
        from .commit_build import build_from_commit
        return build_from_commit(a.repository or root,a.revision,a.destination,toolchain=a.toolchain,build_images=a.build_images,image_prefix=a.image_prefix,product_repository=a.product_repository)
    if a.command=='compare-deployments':
        from .thesis_comparison import compare_deployments
        return compare_deployments(a.left,a.right,left_sha256=a.left_sha256,right_sha256=a.right_sha256)
    if a.command=='analyse':
        from .analysis import analyse
        return analyse(root,a.run_id,a.analysis_id)
    if a.command=='prepare':
        from .prepare import prepare
        return {'manifest':str(prepare(root,root/a.config,a.id,product_snapshot=a.product_snapshot,schedule=a.schedule,benchmark=getattr(a,'benchmark',None),product_lock=getattr(a,'product_lock',None),local_evidence=getattr(a,'local_evidence',None),continuation_dir=getattr(a,'continuation_dir',None),continuation_receipt=getattr(a,'continuation_receipt',None),origin_report=getattr(a,'origin_report',None),origin_seal=getattr(a,'origin_seal',None),origin_uri=getattr(a,'origin_uri',None),matrix_profile=getattr(a,'matrix_profile',None))),'cloud_started':False}
    if a.command=='download':
        from .optional_download import download
        return download(root,Path(a.manifest),a.run_id,a.object_uri,a.destination)
    if a.command=='status':
        from .export import redact_document
        data=Path(os.environ.get('GKE_EVAL_DATA_ROOT',str(root)));state=data/'.work/gke'/identifier(a.run_id)/'execution.json'
        from .optional_download import status as download_status
        return {'run_id':a.run_id,'optional_local_downloads':download_status(root,a.run_id),'execution':redact_document(read(state)) if state.exists() else {'phase':'not started'},'origin':'saved local control state; not a live VM/process observation'}
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
    print(json.dumps(result,indent=2,default=str))
    if args.command=='bootstrap-controller-rbac' and result.get('verified') is not True:return 2
    if args.command=='recover-iap' and result.get('temporary_removal_confirmed') is not True:return 2
    return 0

if __name__=='__main__':raise SystemExit(main())
