"""Run existing controlled contracts and score explicit live-kind records.

No cloud operations or handwritten pass receipt. Missing live records leave a
partial receipt. This command does not create a cluster or substitute fixtures
for the live structure/arbitration population.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from .models import read, sha, write, COMPLETION_V2
from .bundle import _product
from ..diagnostics.thesis_local import qualify, structure, arbitration

KIND_RECORDS=('product-pin.json','structure.json','arbitration.json','placement-retained.json')


def import_kind_records(source,out,pins):
    source=Path(source);out=Path(out)
    missing=[name for name in KIND_RECORDS if not (source/name).is_file()]
    if (source/'product-pin.json').is_file():
        observed=read(source/'product-pin.json')
        if any(observed.get(key)!=pins.get(key) for key in ('karthos_revision','karthos_source_hash')):
            raise ValueError('kind evidence product identity differs from requested product')
    if missing:return {'complete':False,'missing':missing}
    for name,score in (('structure.json',structure),('arbitration.json',arbitration)):
        if score(read(source/name))['state']!='pass':
            raise ValueError('live kind records did not qualify: '+name)
    record=read(source/'structure.json');old=record['replacement'][0]
    retained=read(source/'placement-retained.json')
    if retained.get('snapshot',{}).get('id')!=old['snapshot']:
        raise ValueError('retained kind snapshot differs from replacement reference')
    for name in KIND_RECORDS:
        path=source/name
        if path.is_symlink():raise ValueError('kind evidence symlink refused')
        (out/name).write_bytes(path.read_bytes())
    return {'complete':True,'missing':[], 'files':{name:sha(out/name) for name in KIND_RECORDS}}


def assemble_temporal(out):
    out=Path(out)
    required=('temporal-observations.json','temporal-reset-expiry.log','structure.json','placement-retained.json')
    if any(not (out/name).is_file() for name in required):return False
    log=(out/'temporal-reset-expiry.log').read_text()
    names=('TestMetricCallsTopologyBuilderHandlesSeriesLifecycle','TestMetricCallsTopologyBuilderObservesOnlyIncreases')
    if any('--- PASS: '+name+' (' not in log for name in names):return False
    observations=read(out/'temporal-observations.json')
    if not isinstance(observations,list) or len(observations)!=3:return False
    old=read(out/'structure.json')['replacement'][0]
    if read(out/'placement-retained.json').get('snapshot',{}).get('id')!=old['snapshot']:return False
    write(out/'temporal-history.json',{'counter_and_edge_observations':observations,
        'checks':{'source-filtered-history':True,'reset':True,'expiry':True,'retained-replacement':True},
        'references':{name:sha(out/name) for name in required},
        'scope':'helper assertions plus exact retained live kind snapshot; not a newly run kind scenario'})
    return True


def validate_handshake(document):
    uri=urlsplit(document.get('endpoint',''))
    if uri.scheme!='http' or uri.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('API fixture must use a loopback endpoint')
    if not document.get('before') or not document.get('after'):raise ValueError('fixture bounds unavailable')
    return document


def run(product,product_lock,out,*,vm_url,kind_records=None):
    product=Path(product).resolve();out=Path(out).resolve();root=Path(__file__).resolve().parents[2]
    pins=read(product_lock)
    files,missing,revision,pending=_product(product,pins)
    if missing or pending or not files or revision!=pins['karthos_revision']:
        raise ValueError('exact product source closure is required for local qualification')
    endpoint=urlsplit(vm_url)
    if endpoint.scheme!='http' or endpoint.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('isolated local VictoriaMetrics loopback URL required')
    out.mkdir(parents=True,exist_ok=False)
    commands=[];failures=[]
    env={**os.environ,'KARTHOS_TEST_VM_URL':vm_url,'PYTHONDONTWRITEBYTECODE':'1'}
    def command(argv,name,*,cwd=product,timeout=300):
        started=time.time()
        with (out/name).open('w') as stream:
            result=subprocess.run(argv,cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
        commands.append({'argv':argv,'cwd':str(cwd),'started_at':started,'finished_at':time.time(),
                         'returncode':result.returncode,'log':name,'sha256':sha(out/name)})
        if result.returncode:raise RuntimeError('local contract command failed: '+name)
    try:
        command(['go','test','./pkg/adapters/storage/victoriametrics','-run','^TestQueryViewsVictoriaMetricsIntegration$','-count=1','-v'],'source-query-contract.log')
        command(['go','run',str(root/'evaluation/helpers/qualify_duration.go')],'duration-contract.log')
        (out/'source-semantics.log').write_bytes((out/'source-query-contract.log').read_bytes()+(out/'duration-contract.log').read_bytes())
        command(['go','test','./pkg/backend/ingest','-run','TestMetricCallsTopologyBuilder(HandlesSeriesLifecycle|ObservesOnlyIncreases)','-count=1','-v'],'temporal-reset-expiry.log')
        command(['go','run',str(root/'evaluation/helpers/qualify_temporal.go')],'temporal-observations.json')
        # Compile before the helper starts its bounded one-minute API lifetime.
        binary=out/'api-fixture'
        command(['go','build','-o',str(binary),str(root/'evaluation/helpers/qualify_api.go')],'api-build.log')
        with (out/'api-handshake.json').open('w') as stdout,(out/'api-stderr.log').open('w') as stderr:
            child=subprocess.Popen([str(binary)],cwd=product,env=env,stdout=stdout,stderr=stderr)
            try:
                deadline=time.monotonic()+15
                while True:
                    try:handshake=validate_handshake(read(out/'api-handshake.json'));break
                    except (ValueError,OSError):
                        if child.poll() is not None or time.monotonic()>=deadline:raise RuntimeError('API fixture handshake unavailable')
                        time.sleep(.1)
                command([os.environ.get('PYTHON','python3'),'-B','-m','evaluation.diagnostics.thesis_api_fixture',
                    '--openapi',str(product/'docs/openapi.yaml'),'--handshake',str(out/'api-handshake.json'),
                    '--out',str(out/'api-only.json')],'api-score.log',cwd=root,timeout=30)
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:child.wait(timeout=5)
                    except subprocess.TimeoutExpired:child.kill();child.wait(timeout=5)
        kind=import_kind_records(kind_records or out/'missing-kind-records',out,pins)
        write(out/'kind-import.json',kind)
        assemble_temporal(out)
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        failures.append(str(exc))
    finally:write(out/'commands.json',commands)
    receipt=qualify(out,pins);receipt['protocol']=COMPLETION_V2
    receipt['command_failures']=failures;receipt['scope']='controlled helper execution plus separately captured exact-product live kind records'
    if failures:
        # Never certify a command failure merely because a partial record exists.
        for row in receipt['checks'].values():
            if row.get('state')=='pass':row['state']='insufficient';row['reason']='qualification command did not complete'
    write(out/'local-contract.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product',type=Path,required=True)
    parser.add_argument('--product-lock',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--vm-url',required=True)
    parser.add_argument('--kind-records',type=Path)
    args=parser.parse_args()
    result=run(args.product,args.product_lock,args.out,vm_url=args.vm_url,kind_records=args.kind_records)
    print(json.dumps(result,indent=2))
    return 0 if all(row['state']=='pass' for row in result['checks'].values()) else 2

if __name__=='__main__':raise SystemExit(main())
