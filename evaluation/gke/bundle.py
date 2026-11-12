"""Offline deterministic controller build contexts. No downloads/builds/cloud."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from .models import identifier, implementation_hashes, sha, write
from ..runner import tree_digest

SOURCE_ROOTS=('evaluation','infrastructure','protocols','config','docs')
SOURCE_ENTRYPOINTS=('README.md','gke-eval.sh')
EXCLUDED={'.git','__pycache__','.terraform'}
PRIVATE={'.work','approvals','results','.aws','.config','gcloud','node_modules'}
CREDENTIALS={'credentials','credential','application_default_credentials','service-account-key','id_rsa','id_ed25519','.env','.npmrc','.netrc'}
CACHES={'boutique':'boutique_source_hash','opentelemetry-demo':'astronomy_chart_hash','karthos-chart':'karthos_chart_hash'}
TOOL_VERSIONS=('kubectl','helm','istioctl','terraform','task','go','gcloud','gke-gcloud-auth-plugin')
TOOL_FILES=('bin/kubectl','bin/helm','bin/istioctl','bin/terraform','bin/task','go/bin/go',
            'google-cloud-sdk/bin/gcloud','google-cloud-sdk/bin/gke-gcloud-auth-plugin','google-cloud-sdk/VERSION','requirements.lock','wheels/')


def _safe_name(name):
    p=PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or '\\' in name or str(p)!=name:
        raise ValueError('unsafe bundle path: '+name)
    if set(p.parts)&PRIVATE or '.git' in p.parts:
        raise ValueError('private/Git path forbidden in bundle: '+name)
    for part in p.parts:
        lower=part.lower()
        if lower in CREDENTIALS or lower.split('.')[0] in CREDENTIALS or lower.startswith('credentials.'):
            raise ValueError('credential filename forbidden in bundle: '+name)
    return p


def _source_path(root,name):
    rel=_safe_name(name);root=Path(root)
    if root.is_symlink(): raise ValueError('symlink bundle root forbidden')
    for i in range(1,len(rel.parts)+1):
        if root.joinpath(*rel.parts[:i]).is_symlink():
            raise ValueError('symlink bundle input forbidden: '+name)
    path=root.joinpath(*rel.parts)
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('bundle input escapes root')
    return path


def _scan(root, *, exclude=()):
    """File maps with explicit exclusions; reject links before copying bytes."""
    files={};root=Path(root)
    if root.is_symlink(): raise ValueError('symlink input root forbidden')
    for base,dirs,names in os.walk(root,followlinks=False):
        base=Path(base)
        for name in list(dirs):
            relative=(base/name).relative_to(root)
            if set(relative.parts)&set(exclude): dirs.remove(name);continue
            _source_path(root,str(relative))
        for name in names:
            relative=(base/name).relative_to(root)
            if set(relative.parts)&set(exclude): continue
            path=_source_path(root,str(relative))
            if not path.is_file(): raise ValueError('nonregular bundle input: '+str(relative))
            files[str(relative)]=sha(path)
    return dict(sorted(files.items()))


def _toolchain_path(root,name):
    """Permit public SDK credential-handling code, never user credential state."""
    rel=PurePosixPath(name)
    if rel.is_absolute() or '..' in rel.parts or '\\' in name or str(rel)!=name:
        raise ValueError('unsafe toolchain path')
    sdk_code=len(rel.parts)>2 and rel.parts[0]=='google-cloud-sdk' and rel.parts[1] in ('lib','platform')
    for index,part in enumerate(rel.parts):
        low=part.lower()
        if part in PRIVATE or part=='.git':
            if not (part=='gcloud' and (name=='google-cloud-sdk/bin/gcloud' or sdk_code)):
                raise ValueError('private toolchain path: '+name)
        sensitive=low in CREDENTIALS or low.split('.')[0] in CREDENTIALS or low.startswith('credentials.')
        if sensitive:
            library=sdk_code and (low in ('credentials','credential') and (index<len(rel.parts)-1 or Path(root).joinpath(*rel.parts).is_dir()) or index==len(rel.parts)-1 and low in ('credentials.py','credential.py'))
            if not library:raise ValueError('credential state forbidden in toolchain: '+name)
        if low in ('access_tokens.db','credentials.db','legacy_credentials'):
            raise ValueError('credential state forbidden in toolchain: '+name)
    path=Path(root)
    if path.is_symlink():raise ValueError('symlink toolchain root')
    for part in rel.parts:
        path=path/part
        if path.is_symlink():raise ValueError('symlink toolchain input')
    return path


def scan_toolchain(root):
    files={}
    for path in Path(root).rglob('*'):
        name=str(path.relative_to(root));safe=_toolchain_path(root,name)
        if safe.is_file():files[name]=sha(safe)
    return dict(sorted(files.items()))


def _product_path(root,name):
    # Existing validated snapshot contract hashes file bytes through this exact
    # documentation alias. Materialize those reviewed bytes, never an output link.
    path=Path(root)/name
    if name=='CLAUDE.md' and path.is_symlink() and os.readlink(path)=='AGENTS.md':
        target=_source_path(root,'AGENTS.md')
        if not target.is_file():raise ValueError('documentation alias target is not a regular reviewed file')
        return target
    return _source_path(root,name)

def _copy_files(source,destination,files,*,product=False,toolchain=False):
    for name,expected in sorted(files.items()):
        path=(_product_path if product else _toolchain_path if toolchain else _source_path)(source,name)
        if not path.is_file() or sha(path)!=expected: raise ValueError('reviewed input changed: '+name)
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
        target.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        if sha(target)!=expected: raise ValueError('copied input hash mismatch: '+name)


def _git(root,*args):
    result=subprocess.run(['git',*args],cwd=root,check=True,capture_output=True,text=True)
    return result.stdout


def _product(snapshot,pins):
    """Use the original repository_fingerprint listing/byte algorithm exactly."""
    if snapshot is None or not Path(snapshot).exists():
        return {},[],None,['validated product snapshot is pending']
    root=Path(snapshot)
    if root.is_symlink(): raise ValueError('symlink product snapshot forbidden')
    archived=root.parent/'product-manifest.json'
    if not (root/'.git').exists() and archived.is_file():
        record=json.loads(archived.read_text())
        if record.get('state')!='verified' or record.get('original_revision')!=pins.get('karthos_revision'):
            raise ValueError('archive revision or verification state differs from validated snapshot')
        files=_scan(root)
        if files!=record.get('files'):raise ValueError('archive product file closure differs from recorded checksums')
        missing=record.get('missing_tracked_paths',[]);digest=hashlib.sha256()
        if set(files)&set(missing):raise ValueError('archive missing paths overlap stored files')
        for name in sorted(set(files)|set(missing)):
            _safe_name(name);digest.update(name.encode()+b'\0')
            digest.update((root/name).read_bytes() if name in files else b'<missing>')
            digest.update(b'\0')
        if digest.hexdigest()!=pins.get('karthos_source_hash') or record.get('original_source_hash')!=pins.get('karthos_source_hash'):
            raise ValueError('archive original fingerprint differs from validated snapshot')
        return files,missing,record['original_revision'],[]
    names=sorted(set(_git(root,'ls-files','-z','--cached','--others','--exclude-standard').split('\0'))-{''})
    files={};missing=[];digest=hashlib.sha256()
    for name in names:
        # Git control metadata is never source bytes, even if a malformed index lists it.
        path=_product_path(root,name)
        digest.update(name.encode()+b'\0')
        if path.is_file():
            payload=path.read_bytes();digest.update(payload);files[name]=hashlib.sha256(payload).hexdigest()
        elif path.exists(): raise ValueError('nonregular product source: '+name)
        else: digest.update(b'<missing>');missing.append(name)
        digest.update(b'\0')
    original=digest.hexdigest()
    if not pins.get('karthos_source_hash') or original!=pins['karthos_source_hash']:
        raise ValueError('product snapshot does not match validated original source fingerprint')
    revision=_git(root,'rev-parse','HEAD').strip()
    if not pins.get('karthos_revision') or revision!=pins['karthos_revision']:
        raise ValueError('product snapshot revision differs from validated original revision')
    return dict(sorted(files.items())),missing,revision,[]


def build_bundle(root,bundle_id,product_snapshot,pins,source_hashes,*,toolchain=None,chart_snapshot=None):
    """Return {path, manifest_hashes:{source,product,cache,toolchain}, pending}.

    Creates immutable build/gke/bundles/<bundle_id>. Toolchain is intentionally
    an empty pending manifest until reviewed linux/amd64 files are supplied.
    Product mismatch/private inputs are errors; absent inputs are explicit pending
    closures. No historical evidence or original checkout files are modified.
    """
    identifier(bundle_id);root=Path(root)
    if root.is_symlink(): raise ValueError('symlink evaluation root forbidden')
    dest=root/'build/gke/bundles'/bundle_id
    if dest.exists() or dest.is_symlink(): raise FileExistsError('bundle bundle IDs cannot be overwritten')
    for parent in (root/'build',root/'build/gke',root/'build/gke/bundles'):
        if parent.is_symlink(): raise ValueError('symlink bundle output parent forbidden')
    expected=implementation_hashes(root)
    if source_hashes!=expected: raise ValueError('source checksum map is stale or does not cover exact reviewed implementation')
    for prefix in SOURCE_ROOTS:
        if (root/prefix).exists(): _scan(root/prefix,exclude=EXCLUDED)
    for name in source_hashes: _source_path(root,name)
    product_files,removed,revision,pending=_product(product_snapshot,pins)
    if not product_files and not any('product' in item for item in pending):
        pending.append('validated product has no source bytes')
    caches={};cache_trees={};cache_paths={}
    for name,key in CACHES.items():
        cache=Path(chart_snapshot) if name=='karthos-chart' and chart_snapshot else root/'cache'/name
        if not cache.exists(): pending.append('cache '+name+' is missing');continue
        if not pins.get(key): pending.append('cache '+name+' validated baseline digest is missing');continue
        files=_scan(cache,exclude={'.git'})
        actual=tree_digest(cache,exclude={'.git'})
        if actual!=pins[key]: pending.append('cache '+name+' baseline tree digest mismatch');continue
        caches[name]=files;cache_trees[name]=actual;cache_paths[name]=cache
    for prefix in SOURCE_ROOTS:
        if not any(n.startswith(prefix+'/') for n in source_hashes): pending.append('required reviewed source subtree '+prefix+' is missing')
    for name in SOURCE_ENTRYPOINTS:
        if name not in source_hashes: pending.append('required reviewed source entrypoint '+name+' is missing')
    if 'infrastructure/controller/Dockerfile' not in source_hashes:
        pending.append('reviewed controller Dockerfile is missing')
    tools=None
    if toolchain:
        import importlib.util
        toolchain=Path(toolchain).resolve();manifest=toolchain/'manifest.json'
        spec=importlib.util.spec_from_file_location('reviewed_tool_verifier',root/'infrastructure/controller/verify_bundle.py');verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
        tools=verifier.verify_bundle(toolchain,manifest,sha(manifest),'toolchain')
        scan_toolchain(toolchain)
    else:pending.append('toolchain linux/amd64 reviewed binaries, SDK, hash-locked dependencies and offline wheels are pending')
    dest.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.'+bundle_id+'-',dir=dest.parent))
    try:
        for name in ('source','product','cache','toolchain'): (stage/name).mkdir()
        _copy_files(root,stage/'source',source_hashes)
        if product_files: _copy_files(Path(product_snapshot),stage/'product',product_files,product=True)
        cache_files={}
        for name,files in caches.items():
            (stage/'cache'/name).mkdir()
            _copy_files(cache_paths[name],stage/'cache'/name,files)
            if tree_digest(stage/'cache'/name)!=cache_trees[name]: raise ValueError('copied cache tree digest mismatch')
            cache_files.update({name+'/'+relative:checksum for relative,checksum in files.items()})
        write(stage/'source-manifest.json',{'files':dict(sorted(source_hashes.items()))})
        write(stage/'product-manifest.json',{'files':product_files,'original_source_hash':pins.get('karthos_source_hash'),
              'original_revision':revision,'expected_original_revision':pins.get('karthos_revision'),
              'missing_tracked_paths':removed,'materialized_documentation_aliases':({'CLAUDE.md':'AGENTS.md'} if product_snapshot and (Path(product_snapshot)/'CLAUDE.md').is_symlink() and 'CLAUDE.md' in product_files else {}),'state':'verified' if product_files else 'pending'})
        write(stage/'cache-manifest.json',{'files':dict(sorted(cache_files.items())),'baseline_tree_digests':cache_trees,
              'state':'verified' if len(caches)==len(CACHES) else 'pending'})
        write(stage/'toolchain/manifest.json',{'platform':'linux/amd64','files':{},'versions':{},'state':'pending',
              'pending_versions':list(TOOL_VERSIONS),'pending_files':list(TOOL_FILES),
              'pending_python_dependencies':['google-auth','functions-framework','requests','pyyaml==6.0.3','all transitive hash-locked dependencies']})
        if tools:
            _copy_files(toolchain,stage/'toolchain',tools['files'],toolchain=True)
            write(stage/'toolchain/manifest.json',tools)
        manifest_paths={'source':'source-manifest.json','product':'product-manifest.json',
                        'cache':'cache-manifest.json','toolchain':'toolchain/manifest.json'}
        hashes={name:sha(stage/path) for name,path in manifest_paths.items()}
        docker=stage/'source/infrastructure/controller/Dockerfile'
        if docker.is_file(): shutil.copyfile(docker,stage/'Dockerfile')
        args=' \\\n  '.join('--build-arg '+name.upper()+'_MANIFEST_SHA256='+value for name,value in hashes.items())
        write(stage/'BUILD.md','# Offline controller build context\n\n'
              'Readiness is PENDING. No image has been built or pushed. No tools were downloaded.\n'
              'Supply and review the complete linux/amd64 toolchain closure and immutable base image first; '
              'the empty toolchain manifest intentionally fails the Docker verifier. Changing inputs requires a new review and regenerated manifest hashes.\n\n'
              'Source documentation: `source/docs/`; runtime contract: `source/infrastructure/terraform/CONTRACT.md`; '
              'verifier: `source/infrastructure/controller/verify_bundle.py`. Historical results, approvals, private work directories and Git metadata are excluded.\n\n'
              'After all pending inputs are supplied and reviewed, from this directory:\n\n'
              '```sh\n# Replace IMAGE_NAME and BASE_IMAGE_DIGEST with reviewed values.\n'
              'docker build --platform linux/amd64 --tag IMAGE_NAME \\\n  --build-arg BASE_IMAGE=BASE_IMAGE_DIGEST \\\n  '+args+' \\\n  --file source/infrastructure/controller/Dockerfile .\n```\n\n'
              'Manifest SHA arguments above pin the current bytes, including the pending toolchain manifest. '
              'Do not build from this pending context or reuse these hashes after replacing inputs. No push command is included.\n\nPending:\n'+''.join('- '+item+'\n' for item in pending))
        write(stage/'bundle-status.json',{'state':'pending','pending':pending,'manifest_hashes':hashes})
        if implementation_hashes(root)!=source_hashes: raise ValueError('reviewed source changed during bundle creation')
        if product_files:
            digest=hashlib.sha256()
            for name in sorted(set(product_files)|set(removed)):
                digest.update(name.encode()+b'\0')
                digest.update((stage/'product'/name).read_bytes() if name in product_files else b'<missing>')
                digest.update(b'\0')
            if digest.hexdigest()!=pins['karthos_source_hash']: raise ValueError('copied product original fingerprint mismatch')
        stage.rename(dest)
    except Exception:
        shutil.rmtree(stage);raise
    return {'path':str(dest),'manifest_hashes':hashes,'pending':pending}
