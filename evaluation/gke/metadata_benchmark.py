"""Replay preserved metadata through old/new publication; never contacts GCP.

Archive acquisition/seal validation are outside timed trials. Transport is an
in-memory/filesystem emulator exercising immutable creation and SHA readback;
its wall time is NOT a measurement of GCS latency or e2-standard-4 throughput.
"""
from __future__ import annotations
import argparse,hashlib,json,platform,shutil,statistics,subprocess,tarfile,tempfile,time
from pathlib import Path,PurePosixPath
from types import SimpleNamespace
from . import export
from .failure_evidence import NAMES,JSON_LIMIT,retain_attempt_reports
from .models import read,write,sha,implementation_hashes,canonical_sha

GRAPH_NAMES=('api-nodes.json','api-edges.json','versions-after.json','consumer/ledger.json')
READINESS_NAMES=('k8s-pods.json','karthos-pods.json','k8s-nodes.json','setup-resolved-plan.json','setup-effective-settings.json','api-nodes.json')


def preserved_metadata(archive,destination):
    """Verify every original member against its internal seal, extracting only small metadata."""
    selected=set(NAMES)|set(GRAPH_NAMES)|set(READINESS_NAMES)
    hashes={};sizes={};seal=None;total=0
    with tarfile.open(archive,'r|gz') as bundle:
        for member in bundle:
            name=PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or '..' in name.parts or member.name in hashes:
                raise ValueError('unsafe or duplicate preserved member')
            stream=bundle.extractfile(member);digest=hashlib.sha256();payload=[]
            keep=(member.name in selected and member.size<=JSON_LIMIT) or member.name=='GKE-SHA256SUMS'
            while chunk:=stream.read(1024*1024):
                digest.update(chunk)
                if keep:payload.append(chunk)
            hashes[member.name]=digest.hexdigest();sizes[member.name]=member.size;total+=member.size
            if keep:
                raw=b''.join(payload)
                if member.name=='GKE-SHA256SUMS':seal=raw.decode()
                else:
                    target=destination/member.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    if seal is None:raise ValueError('preserved seal unavailable')
    declared=dict((name,digest) for digest,name in (line.split('  ',1) for line in seal.splitlines()))
    actual={name:digest for name,digest in hashes.items() if name!='GKE-SHA256SUMS'}
    if declared!=actual:raise ValueError('preserved member checksums or population mismatch')
    return {'archive_sha256':sha(archive),'member_hashes':hashes,'member_bytes':sizes,'expanded_bytes':total,
            'verified_members':len(actual),'extraction':'selected JSON <= existing 8MiB report bound; all member checksums verified'}


class LocalTransport:
    def __init__(self,root):
        self.root=root;self.store=root/'objects';self.store.mkdir();self.calls=[]
    def command(self,args,**kwargs):
        self.calls.append(list(args))
        if args[:4]==['gcloud','storage','objects','describe']:
            target=self.store/hashlib.sha256(args[4].encode()).hexdigest()
            return SimpleNamespace(returncode=0 if target.exists() else 1,stdout='{}',stderr='' if target.exists() else '404 NotFound')
        if args[:3]!=['gcloud','storage','cp']:raise ValueError('no external commands permitted')
        if args[3]=='--if-generation-match=0':
            source=Path(args[4]);target=self.store/hashlib.sha256(args[5].encode()).hexdigest()
            with target.open('xb') as out,source.open('rb') as inp:shutil.copyfileobj(inp,out)
        else:
            source=self.store/hashlib.sha256(args[3].encode()).hexdigest();shutil.copyfile(source,args[4])
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    def payloads(self):
        result={}
        for p in self.store.iterdir():
            if tarfile.is_tarfile(p):
                with tarfile.open(p) as bundle:
                    for m in bundle:
                        if m.isfile():result[m.name]=hashlib.sha256(bundle.extractfile(m).read()).hexdigest()
        return result


def trial(source,root,category,bundled):
    transport=LocalTransport(root);ctx=SimpleNamespace(result=root,command=transport.command)
    began=time.monotonic();cpu=time.process_time()
    if bundled:receipt=export.upload_bundle(ctx,source,bucket='offline-evidence',prefix='benchmark/'+category,category=category)
    else:receipt=export.upload(ctx,source,bucket='offline-evidence',prefix='benchmark/'+category)
    elapsed=time.monotonic()-began
    files=list(transport.store.iterdir())
    return {'seconds':elapsed,'cpu_seconds':time.process_time()-cpu,'objects':len(files),
            'sdk_commands':len(transport.calls),'uploaded_bytes':sum(p.stat().st_size for p in files),
            'verified':receipt['verified'],'member_hashes':transport.payloads() if bundled else {
                args[4].split('/benchmark/'+category+'/',1)[-1]:sha(transport.store/hashlib.sha256(args[4].encode()).hexdigest())
                for args in transport.calls if args[:4]==['gcloud','storage','objects','describe']} }


def run(archives,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False);cases=[]
    code_root=Path(__file__).resolve().parents[2];code_hashes=implementation_hashes(code_root)
    with tempfile.TemporaryDirectory(prefix='metadata-replay-') as tmp:
        root=Path(tmp).resolve();records=[]
        for i,archive in enumerate(archives):
            source=root/f'input-{i}';source.mkdir();proof=preserved_metadata(Path(archive),source)
            # These descriptors describe a reconstructed, offline receipt. They
            # are never supplied to a live run or represented as fresh GCS proof.
            members={name:{'sha256':proof['member_hashes'][name],'source_sha256':proof['member_hashes'][name],
                           'bytes':proof['member_bytes'][name]} for name in NAMES if name in proof['member_hashes']}
            receipt={'verified':True,'uri':f'gs://offline-evidence/window-{i}/','archive_index':'window-index.json',
                     'objects':{'window-index.json':{'sha256':'a'*64,'bytes':1},'window.tar.gz':{'sha256':proof['archive_sha256'],'bytes':Path(archive).stat().st_size}},
                     'retained_members':members,'scope':'OFFLINE reconstructed receipt; not provider verification'}
            record={'id':f'w{i}','attempts':[{'attempt_id':f'w{i}-a1','path':str(source),'export':receipt}]};records.append(record)
            readiness=root/f'readiness-{i}';readiness.mkdir()
            for name in READINESS_NAMES:
                if (source/name).exists():shutil.copy2(source/name,readiness/Path(name).name)
            write(readiness/'receipt.json',{'scope':'reconstructed readiness payload using preserved inventories; no operational pass asserted'})
            cases.append({'name':str(Path(archive).name),'proof':{k:v for k,v in proof.items() if not k.startswith('member_')},'trials':[]})
            for repeat in range(3):
                values={}
                for candidate in ([False,True] if repeat%2==0 else [True,False]):
                    work=root/f'trial-{i}-{repeat}-{candidate}';work.mkdir()
                    checkpoint=work/'checkpoint';checkpoint.mkdir()
                    rows=json.loads(json.dumps(records))
                    if not candidate:
                        for row in rows:
                            for attempt in row['attempts']:attempt['export']={}
                    retain_attempt_reports(SimpleNamespace(write=write),rows,checkpoint)
                    for j,row in enumerate(records):
                        for name in GRAPH_NAMES:
                            src=Path(row['attempts'][0]['path'])/name
                            if src.exists():
                                target=checkpoint/'retained'/row['id']/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
                    write(checkpoint/'checkpoint.json',{'records':records,'scope':'reconstructed cumulative checkpoint'})
                    rdir=work/'readiness';rdir.mkdir();cdir=work/'publication';cdir.mkdir()
                    values['candidate' if candidate else 'baseline']={'readiness':trial(readiness,rdir,'readiness',candidate),'checkpoint':trial(checkpoint,cdir,'checkpoint',candidate)}
                    shutil.rmtree(work)
                # Original graph snapshots and readiness documents must survive
                # identically; only extra provenance/packaging may differ.
                for category in ('readiness','checkpoint'):
                    old=values['baseline'][category]['member_hashes'];new=values['candidate'][category]['member_hashes']
                    compared={k:v for k,v in old.items() if k not in ('EXPORT-PROJECTION.json','GKE-SHA256SUMS') and (category=='readiness' or k.startswith('retained/'))}
                    if any(new.get(k)!=v for k,v in compared.items()):raise ValueError('unchanged metadata bytes differ')
                    values['candidate'][category]['equivalent_retained_members']=len(compared)
                cases[-1]['trials'].append(values)
            print('completed',Path(archive).name,flush=True)
    summary={}
    for category in ('readiness','checkpoint'):
        summary[category]={mode:{key:statistics.median(t[mode][category][key] for c in cases for t in c['trials']) for key in ('seconds','cpu_seconds','objects','sdk_commands','uploaded_bytes')} for mode in ('baseline','candidate')}
    if implementation_hashes(code_root)!=code_hashes:raise ValueError('implementation changed during replay')
    report={'scope':'offline preserved-input replay; transport latency emulated, no GCP calls','environment':platform.platform(),
            'implementation_sha256':canonical_sha(code_hashes),'implementation_hashes':code_hashes,
            'hardware_scope':'local Linux container; not an e2-standard-4 timing measurement',
            'cases':cases,'summary':summary,'trials_per_case':3,'ordering':'alternating baseline/candidate','cloud_started':False}
    write(out/'benchmark.json',report);return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',action='append',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(run(args.archive,args.out)['summary'],indent=2))
