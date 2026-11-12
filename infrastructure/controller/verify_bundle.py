"""Offline build input verification; no downloads, cloud or approval creation."""
import hashlib
import json
import re
import sys
from pathlib import Path

REQUIRED_NATIVE = {'kubectl':'bin/kubectl', 'helm':'bin/helm', 'istioctl':'bin/istioctl',
                   'terraform':'bin/terraform', 'gke-gcloud-auth-plugin':'bin/gke-gcloud-auth-plugin', 'task':'bin/task', 'go':'go/bin/go'}
EXCLUDED = {'__pycache__', '.terraform', '.git'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_bundle(root, manifest_path, manifest_sha256, kind):
    root, manifest_path = Path(root), Path(manifest_path)
    if not re.fullmatch('[0-9a-f]{64}', manifest_sha256 or ''):
        raise ValueError('reviewed manifest SHA256 required')
    if not manifest_path.is_file() or digest(manifest_path) != manifest_sha256:
        raise ValueError('missing or mismatched reviewed bundle manifest')
    manifest = json.loads(manifest_path.read_text())
    files = manifest.get('files')
    if not isinstance(files, dict) or (not files and kind != 'cache'):
        raise ValueError('nonempty reviewed file checksum map required')
    for name, checksum in files.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe manifest path')
        path = root / relative
        if any(root.joinpath(*relative.parts[:index]).is_symlink() for index in range(1, len(relative.parts)+1)):
            raise ValueError('bundle symlinks are forbidden')
        if not path.is_file() or digest(path) != checksum:
            raise ValueError('bundle file hash mismatch: ' + name)
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('bundle symlinks are forbidden')
        if not path.is_file() or path == manifest_path:
            continue
        relative = path.relative_to(root)
        if kind == 'source':
            if set(relative.parts) & EXCLUDED or path.suffix in {'.pyc', '.tfstate', '.tfplan'} or path.name.endswith('.backup'):
                continue
            if relative.parts[0] not in {'evaluation','infrastructure','protocols','config','docs','README.md','gke-eval.sh'}:
                continue
        if kind == 'product' and '.git' in relative.parts:
            raise ValueError('product must be a reviewed source archive without Git metadata')
        actual.add(str(relative))
    if actual != set(files):
        raise ValueError('bundle manifest does not cover exact file closure')
    if kind == 'source':
        for required in ['evaluation', 'infrastructure', 'protocols', 'config', 'docs']:
            if not any(name.startswith(required + '/') for name in files):
                raise ValueError('required reviewed source absent: ' + required)
        if not {'README.md', 'gke-eval.sh'} <= set(files):
            raise ValueError('required reviewed source entrypoints absent')
    if kind == 'toolchain':
        if manifest.get('platform') != 'linux/amd64':
            raise ValueError('only reviewed linux/amd64 toolchain supported')
        versions = manifest.get('versions', {})
        for command, name in REQUIRED_NATIVE.items():
            if name not in files or not versions.get(command):
                raise ValueError('missing pinned runtime tool: ' + command)
            data = (root / name).read_bytes()[:20]
            if data[:5] != b'\x7fELF\x02' or data[5] != 1 or data[18:20] != b'\x3e\x00':
                raise ValueError('runtime binary must be Linux ELF amd64: ' + command)
            if not (root / name).stat().st_mode & 0o111:
                raise ValueError('runtime binary not executable: ' + command)
        for name in ['google-cloud-sdk/bin/gcloud', 'google-cloud-sdk/VERSION', 'requirements.lock']:
            if name not in files:
                raise ValueError('missing SDK or Python dependency lock: ' + name)
        if not versions.get('gcloud') or not any(name.startswith('wheels/') for name in files):
            raise ValueError('pinned SDK version and offline dependency wheels required')
        lock = (root / 'requirements.lock').read_text()
        for package in ['google-auth', 'functions-framework', 'requests', 'PyYAML']:
            if package.lower() not in lock.lower():
                raise ValueError('required runtime Python dependency absent: ' + package)
        if '--hash=sha256:' not in lock:
            raise ValueError('hash-locked transitive Python dependencies required')
    return manifest


if __name__ == '__main__':
    verify_bundle(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4])
