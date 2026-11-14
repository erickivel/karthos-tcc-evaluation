"""Acquire and build a reviewed evaluation commit without historical result inputs.

This explicit network/build operation never provisions GCP or publishes images.
Offline prepare remains a separate command. Outputs are new, immutable directories.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import urllib.request
import urllib.parse
import zipfile

from .models import read, sha, write, implementation_hashes
from .bundle import build_bundle, scan_toolchain
from ..runner import tree_digest, repository_fingerprint

INPUT_DIR=Path('protocols/thesis-completion-v2/inputs')


def frozen_inputs(root):
    """Small tracked settings, not an exported study or an accuracy receipt."""
    value=read(Path(root)/INPUT_DIR/'experiment-inputs.json')
    if value.get('schema_version')!=1 or not all(k in value for k in ('pins','image_lock','thresholds','baseline_config')):
        raise ValueError('standalone experiment inputs are incomplete')
    return value


def _run(argv, *, cwd=None):
    return subprocess.run([str(a) for a in argv],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()


def checkout(repository,revision,destination):
    if not re.fullmatch('[a-f0-9]{40}',revision):raise ValueError('full evaluation/product commit SHA required')
    destination=Path(destination)
    if destination.exists():raise FileExistsError(destination)
    # Clone the committed object database, never copy a dirty working tree.
    destination.mkdir(parents=True)
    _run(['git','init'],cwd=destination)
    _run(['git','fetch','--depth=1','--',repository,revision],cwd=destination)
    _run(['git','checkout','--detach','FETCH_HEAD'],cwd=destination)
    if _run(['git','rev-parse','HEAD'],cwd=destination)!=revision:raise ValueError('checkout revision mismatch')
    if _run(['git','status','--porcelain'],cwd=destination):raise ValueError('checkout not clean')


def download(record,destination):
    destination=Path(destination)
    if not str(record['url']).startswith('https://') or not re.fullmatch('[a-f0-9]{64}',record['sha256']):
        raise ValueError('HTTPS URL and pinned SHA-256 required')
    if destination.exists():
        if sha(destination)!=record['sha256']:raise ValueError('cached download checksum mismatch')
        return destination
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.partial')
    try:
        request=record['url']
        if record.get('docker_repository'):
            repository=record['docker_repository']
            if not re.fullmatch(r'[a-z0-9_-]+/[a-z0-9_-]+',repository) or not request.startswith('https://registry-1.docker.io/v2/'+repository+'/blobs/sha256:'):
                raise ValueError('public OCI download scope does not match URL')
            token_url='https://auth.docker.io/token?service=registry.docker.io&scope='+urllib.parse.quote('repository:'+repository+':pull')
            with urllib.request.urlopen(token_url,timeout=30) as response:token=json.load(response)['token']
            request=urllib.request.Request(request,headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(request,timeout=120) as response,temporary.open('wb') as output:
            shutil.copyfileobj(response,output)
        if sha(temporary)!=record['sha256']:raise ValueError('download checksum mismatch: '+record['url'])
        temporary.rename(destination)
    finally:temporary.unlink(missing_ok=True)
    return destination


def extract(archive,destination, *, sdk=False):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive) as source:
        members=source.getmembers()
        for member in members:
            name=PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or '\\' in member.name:
                raise ValueError('unsafe archive path')
            if member.issym() or member.islnk():
                # The SDK includes optional bundled-Python links. It runs with
                # the pinned controller Python instead; none are materialized.
                if sdk:continue
                raise ValueError('archive links are not build inputs')
            if not member.isfile() and not member.isdir():raise ValueError('nonregular archive member')
            if sdk and ('platform/bundledpython' in member.name or '__pycache__' in name.parts):continue
            source.extract(member,destination,filter='data')


def pinned_dockerfile(text,bases):
    lines=[]
    for line in text.splitlines():
        if line.startswith('FROM '):
            parts=line.split();base=parts[1]
            if base not in bases:raise ValueError('unreviewed product build base: '+base)
            if not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',bases[base]):raise ValueError('immutable product base required')
            parts[1]=bases[base];line=' '.join(parts)
        if line.strip()=='RUN npm install':line='RUN npm ci'
        lines.append(line)
    return '\n'.join(lines)+'\n'


def _tools(root,lock,downloads,base):
    tools=root/'toolchain';tools.mkdir();(tools/'bin').mkdir()
    for name in ('kubectl','helm','terraform','task','istioctl','go','gcloud','gke-gcloud-auth-plugin'):
        item=lock['downloads'][name];archive=download(item,downloads/(name+'.download'))
        if name=='kubectl':shutil.copyfile(archive,tools/'bin/kubectl')
        elif name=='terraform':
            with zipfile.ZipFile(archive) as source:(tools/'bin/terraform').write_bytes(source.read('terraform'))
        elif name in ('go','gcloud'):extract(archive,tools,sdk=name=='gcloud')
        else:
            with tarfile.open(archive) as source:
                members=[m for m in source.getmembers() if m.isfile() and PurePosixPath(m.name).name==name]
                if len(members)!=1:raise ValueError('ambiguous tool binary: '+name)
                (tools/'bin'/name).write_bytes(source.extractfile(members[0]).read())
        if (tools/'bin'/name).exists():(tools/'bin'/name).chmod(0o755)
    # SDK tooling expects its plugin beside gcloud as well as on PATH.
    shutil.copyfile(tools/'bin/gke-gcloud-auth-plugin',tools/'google-cloud-sdk/bin/gke-gcloud-auth-plugin')
    (tools/'google-cloud-sdk/bin/gke-gcloud-auth-plugin').chmod(0o755)
    shutil.copyfile(root/'source'/INPUT_DIR/'requirements.lock',tools/'requirements.lock')
    (tools/'wheels').mkdir()
    _run(['docker','run','--rm','--platform','linux/amd64','-v',str(tools)+':/toolchain',base,
          'python','-m','pip','download','--only-binary=:all:','--require-hashes','-r','/toolchain/requirements.lock','-d','/toolchain/wheels'])
    for path in tools.rglob('__pycache__'):
        if path.is_dir():shutil.rmtree(path)
    write(tools/'manifest.json',{'platform':'linux/amd64','files':scan_toolchain(tools),'versions':lock['versions']})
    return tools


def _expand_chart_dependencies(chart):
    """Helm published charts may package dependencies; runtime hashes use trees."""
    for archive in sorted(chart.rglob('*.tgz')):
        extract(archive,archive.parent)
        archive.unlink()
    # Nested dependencies may have appeared during the first pass.
    if list(chart.rglob('*.tgz')):_expand_chart_dependencies(chart)


def build_from_commit(repository,revision,destination,*,toolchain=None,build_images=False,image_prefix='karthos-eval',product_repository=None):
    destination=Path(destination).resolve()
    if destination.exists():raise FileExistsError('build destination must be new: '+str(destination))
    if not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*',image_prefix):raise ValueError('invalid image prefix')
    destination.mkdir(parents=True)
    source=destination/'source'
    checkout(repository,revision,source)
    inputs=frozen_inputs(source);lock=read(source/INPUT_DIR/'build-lock.json');pins=inputs['pins']
    product=destination/'product';checkout(product_repository or lock['product_repository'],pins['karthos_revision'],product)
    if repository_fingerprint(product)!=pins['karthos_source_hash']:raise ValueError('pinned product source mismatch')
    cache=source/'cache';cache.mkdir(exist_ok=True)
    checkout(lock['boutique_repository'],pins['boutique_commit'],cache/'boutique')
    shutil.rmtree(cache/'boutique/.git')
    downloads=destination/'downloads'
    astronomy=download(lock['downloads']['astronomy'],downloads/'astronomy.tgz')
    extract(astronomy,cache);_expand_chart_dependencies(cache/'opentelemetry-demo')
    chart=cache/'karthos-chart';shutil.copytree(product/'deploy/helm/karthos',chart)
    (chart/'charts').mkdir(exist_ok=True)
    for name in ('postgresql-18.5.11.tgz','victoria-metrics-single-0.33.0.tgz'):
        shutil.copyfile(download(lock['downloads'][name],downloads/name),chart/'charts'/name)
    for name,key in [('boutique','boutique_source_hash'),('opentelemetry-demo','astronomy_chart_hash'),('karthos-chart','karthos_chart_hash')]:
        if tree_digest(cache/name,exclude={'.git'})!=pins[key]:raise ValueError('acquired cache differs from frozen tree: '+name)
    tools=Path(toolchain).resolve() if toolchain else _tools(destination,lock,downloads,lock['controller_base'])
    bundle=build_bundle(source,'commit-'+revision[:12],product,pins,implementation_hashes(source),toolchain=tools,chart_snapshot=chart)
    if bundle['pending']:raise ValueError('build closure incomplete: '+', '.join(bundle['pending']))
    product_lock={'schema_version':1,**{key:pins[key] for key in ('karthos_revision','karthos_source_hash','karthos_chart_hash','openapi_sha256')},'chart_path':str(chart),'images':{}}
    write(destination/'product-lock.json',product_lock)
    images={}
    if build_images:
        context=Path(bundle['path']);tag='commit-'+revision[:12]
        for name,dockerfile,build_context in [('karthos-backend',product/'deploy/images/backend/Dockerfile',product),('karthos-frontend',product/'web/Dockerfile',product/'web')]:
            reviewed=destination/(name+'.Dockerfile');reviewed.write_text(pinned_dockerfile(dockerfile.read_text(),lock['product_bases']))
            image=image_prefix+'/'+name+':'+tag
            _run(['docker','build','--platform','linux/amd64','-t',image,'-f',reviewed,build_context]);images[name]=image
        controller=image_prefix+'/controller:'+tag
        args=['docker','build','--platform','linux/amd64','-t',controller,'--build-arg','BASE_IMAGE='+lock['controller_base']]
        for name,value in bundle['manifest_hashes'].items():args+=['--build-arg',name.upper()+'_MANIFEST_SHA256='+value]
        _run([*args,'-f',context/'source/infrastructure/controller/Dockerfile',context]);images['controller']=controller
        watchdog=image_prefix+'/watchdog:'+tag
        _run(['docker','build','--platform','linux/amd64','-t',watchdog,'--build-arg','BASE_IMAGE='+lock['controller_base'],
              '--build-arg','WATCHDOG_SOURCE_SHA256='+sha(source/'infrastructure/watchdog/main.py'),'-f',context/'source/infrastructure/watchdog/Dockerfile',context]);images['watchdog']=watchdog
    receipt={'schema_version':1,'evaluation_revision':revision,'source':str(source),'product':str(product),'toolchain':str(tools),
             'product_lock':str(destination/'product-lock.json'),'bundle':bundle,'build_lock_sha256':sha(source/INPUT_DIR/'build-lock.json'),
             'images':{name:{'tag':tag,'image_id':_run(['docker','image','inspect',tag,'--format','{{.Id}}'])} for name,tag in images.items()},
             'state':'built-not-published' if build_images else 'inputs-verified-not-built','cloud_started':False,
             'local_contract':'generate against this exact product before prepare; no historical pass is imported'}
    write(destination/'build-receipt.json',receipt)
    return receipt


def record_publication(receipt_path):
    """After explicit docker push, bind local image IDs to their RepoDigests.

    This does not push or grant permissions. Launch preflight still verifies the
    provider digest and attached-controller access before cloud execution.
    """
    receipt_path=Path(receipt_path);receipt=read(receipt_path);images=receipt.get('images',{})
    if set(images)!={'karthos-backend','karthos-frontend','controller','watchdog'}:raise ValueError('all four built images required')
    published={}
    for name,record in images.items():
        inspected=json.loads(_run(['docker','image','inspect',record['tag']]))[0]
        if inspected['Id']!=record['image_id']:raise ValueError('built image tag changed: '+name)
        repository=record['tag'].rsplit(':',1)[0]
        candidates=[d for d in inspected.get('RepoDigests',[]) if d.startswith(repository+'@sha256:')]
        if len(candidates)!=1:raise ValueError('publish this built image first: '+record['tag'])
        published[name]=candidates[0]
    lock=read(receipt['product_lock']);lock['images']={k:published[k] for k in ('karthos-backend','karthos-frontend')}
    output=receipt_path.parent/'published-product-lock.json'
    if output.exists():raise FileExistsError(output)
    write(output,lock);write(receipt_path.parent/'published-images.json',published)
    return {'product_lock':str(output),'images':published,'cloud_started':False}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-publication',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(record_publication(args.record_publication),indent=2))
