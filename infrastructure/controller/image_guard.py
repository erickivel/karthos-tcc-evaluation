"""Native pre-traffic verification of every frozen packaged file; no network calls."""
import hashlib,json
from pathlib import Path

LOCATIONS={'source':('opt/evaluation','opt/source-manifest.json'),'product':('opt/product','opt/product-manifest.json'),'cache':('opt/evaluation/cache','opt/cache-manifest.json'),'toolchain':('opt/toolchain','opt/toolchain/manifest.json')}

def verify_image(root,experiment):
    root=Path(root);counts={}
    for kind,(directory,manifest) in LOCATIONS.items():
        path=root/manifest
        if not path.is_file() or path.is_symlink():raise ValueError('missing native manifest: '+kind)
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=experiment['bundle']['manifest_hashes'][kind]:raise ValueError('native manifest hash mismatch: '+kind)
        files=json.loads(data).get('files')
        if not isinstance(files,dict) or not files:raise ValueError('empty native file closure: '+kind)
        for relative,expected in files.items():
            rel=Path(relative)
            if rel.is_absolute() or '..' in rel.parts:raise ValueError('unsafe native file path')
            base=root/directory;p=base/rel
            if any(base.joinpath(*rel.parts[:i]).is_symlink() for i in range(1,len(rel.parts)+1)):raise ValueError('native file symlink: '+relative)
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('native file hash mismatch: '+kind+'/'+relative)
        counts[kind]=len(files)
    return counts


def verify_executable_directory(directory):
    """COS /var is noexec; verify the actual persistent runtime mount."""
    import os,subprocess,tempfile
    directory=Path(directory)
    fd,name=tempfile.mkstemp(prefix='eval-exec-probe-',dir=directory)
    probe=Path(name)
    try:
        with os.fdopen(fd,'wb') as file:file.write(b'#!/bin/sh\nexit 0\n')
        probe.chmod(0o700)
        subprocess.run([str(probe)],check=True,capture_output=True,timeout=10)
    finally:
        probe.unlink(missing_ok=True)
