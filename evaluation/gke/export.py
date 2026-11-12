"""Immutable evidence sealing and verified GCS export; operational state is excluded."""
from __future__ import annotations
import base64,copy,csv,hashlib,html,json,re,subprocess,tempfile,shutil,tarfile,gzip,io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from .models import sha,write,read
from .timing import timed

MAX_UPLOAD_WORKERS = 2

FORBIDDEN={'credentials.json','application_default_credentials.json','terraform.tfstate','terraform.tfstate.backup','kubeconfig','approval-private-key.pem'}
def _private_evidence_path(path):
    return (path.name in FORBIDDEN or path.suffix in {'.tfstate', '.tfplan', '.pem', '.key'}
            or '.work' in path.parts or '.terraform' in path.parts)


def _unsafe_archive_path(path, name):
    return path.is_absolute() or '..' in path.parts or _private_evidence_path(path) or name.splitlines() != [name]


def evidence_files(root):
    root=Path(root).resolve();files=[]
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise ValueError('symlink in research evidence: '+str(p))
        if p.is_file():
            if _private_evidence_path(p) or str(p.relative_to(root)).splitlines() != [str(p.relative_to(root))]:
                raise ValueError('unsafe or private operational file in research evidence: ' + str(p))
            files.append(p)
    return files

def seal(root):
    root=Path(root).resolve();files=[p for p in evidence_files(root) if p.name!='GKE-SHA256SUMS']
    write(root/'GKE-SHA256SUMS',''.join(sha(p)+'  '+str(p.relative_to(root))+'\n' for p in files))
    return verify(root)

def verify(root):
    root=Path(root).resolve();path=root/'GKE-SHA256SUMS';errors=[];checked=0
    if not path.exists():return {'valid':False,'errors':['seal missing'],'checked':0}
    for line in path.read_text().splitlines():
        expected,name=line.split('  ',1);p=root/name
        if not p.resolve().is_relative_to(root) or p.is_symlink():errors.append('path escape: '+name);continue
        if not p.is_file() or sha(p)!=expected:errors.append('checksum mismatch: '+name)
        checked+=1
    actual={str(p.relative_to(root)) for p in evidence_files(root) if p.name!='GKE-SHA256SUMS'}
    declared={line.split('  ',1)[1] for line in path.read_text().splitlines()}
    if actual!=declared:errors.append('unsealed file set differs')
    return {'valid':not errors,'errors':errors,'checked':checked}

def _upload(ctx,path,*,bucket,prefix):
    path=Path(path).resolve();status=seal(path)
    if not status['valid']:raise ValueError('local export seal invalid')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]',bucket):raise ValueError('invalid evidence bucket')
    uri='gs://'+bucket+'/'+prefix.strip('/')+'/'
    # Upload sealed bundle to a unique immutable prefix; never sync mutable .work.
    def transfer(p):
        dest=uri+str(p.relative_to(path))
        exists=ctx.command(['gcloud','storage','objects','describe',dest,'--format=json'],timeout=60,cleanup=True,check=False)
        if exists.returncode:
            if not any(x in str(exists.stderr) for x in ('404','NotFound','not found','not exist')):raise RuntimeError('cannot distinguish missing object from access/export failure: '+dest)
            with timed(ctx,'upload') as info:
                info.update(prefix=prefix, member=str(p.relative_to(path)), bytes=p.stat().st_size,
                            usage_scope='overlapping controller and child totals; use publication-transfer for aggregate CPU')
                ctx.command(['gcloud','storage','cp','--if-generation-match=0',str(p),dest],timeout=300,cleanup=True)
        # Read back to verify content, not merely a successful PUT response.
        with tempfile.TemporaryDirectory(prefix='gke-export-') as t:
            with timed(ctx,'readback') as info:
                info.update(prefix=prefix, member=str(p.relative_to(path)), bytes=p.stat().st_size,
                            usage_scope='overlapping controller and child totals; use publication-transfer for aggregate CPU')
                local=Path(t)/'object';ctx.command(['gcloud','storage','cp',dest,str(local)],timeout=300,cleanup=True)
                if sha(local)!=sha(p):raise ValueError('GCS readback checksum mismatch: '+dest)
    # Transfers are independent sealed files; traffic remains serial. Bound SDK
    # process startup on the four-core controller without changing the GCS layout.
    # The executor waits for active transfers even on failure: no ACK or orphan
    # upload can escape this publication call. Every member retains its own
    # generation-zero write and complete checksum readback.
    files=evidence_files(path)
    with timed(ctx,'publication-transfer') as info:
        info.update(prefix=prefix, files=len(files), workers=min(MAX_UPLOAD_WORKERS,len(files)),
                    bytes=sum(p.stat().st_size for p in files))
        with ThreadPoolExecutor(max_workers=MAX_UPLOAD_WORKERS) as pool:
            list(pool.map(transfer,files))
    return {'verified':True,'uri':uri,'files':status['checked']+1,'retention_days':90,'secondary_export_verified':False}

def render_matrix(root,matrix,records,*,unattempted_reason=None):
    root=Path(root);by_id={r['id']:r for r in records};rows=[]
    for w in matrix:
        observed = by_id.get(w['id'], {})
        row = dict(w, **{k:v for k,v in observed.items() if k not in w and k!='state'})
        row.update(execution_mode=w.get('execution'), execution=observed.get('execution','unattempted'),
                   state=observed.get('state','unattempted'))
        rows.append(row)
    if unattempted_reason:
        for row in rows:
            if row['state']=='unattempted':row['reason']=unattempted_reason
    write(root/'matrix.json',rows)
    fields=['id','repeat','phase','cell','generator','scenario','duration_seconds','state','execution_mode','execution','evidence_valid','application_outcomes','numerical_agreement','first_attempt_state','attempt_count','representative_attempt','reason','path']
    with (root/'matrix.csv').open('w',newline='') as out:
        writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    table=''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k,'')))+'</td>' for k in fields)+'</tr>' for r in rows)
    write(root/'matrix.html','<!doctype html><meta charset="utf-8"><title>GKE qualification matrix</title><h1>GKE qualification matrix</h1><p>Execution, evidence validity and numerical agreement are separate.</p><table border="1"><thead><tr>'+''.join('<th>'+k+'</th>' for k in fields)+'</tr></thead><tbody>'+table+'</tbody></table>')
    attempts=[dict(attempt,logical_id=row['id']) for row in records for attempt in row.get('attempts',[])]
    if attempts:
        write(root/'attempts.json',attempts)
        attempt_fields=['logical_id','attempt_id','state','execution','evidence_valid','application_outcomes','numerical_agreement','reason','path']
        with (root/'attempts.csv').open('w',newline='') as out:
            writer=csv.DictWriter(out,fieldnames=attempt_fields,extrasaction='ignore');writer.writeheader();writer.writerows(attempts)
        with (root/'matrix.html').open('a') as out:
            out.write('<h2>All attempts (earlier failures retained)</h2><table border=1>'+''.join('<tr>'+''.join('<td>'+html.escape(str(a.get(k,'')))+'</td>' for k in attempt_fields)+'</tr>' for a in attempts)+'</table>')
    return rows


REDACTED = '<redacted>'
SENSITIVE = re.compile(r'(password|passwd|secret|client_secret|private_key|access_token|refresh_token|authorization|token)', re.I)
SENSITIVE_ENV_NAME = re.compile(r'password|passwd|secret|token|credential', re.I)
# Inline operational manifests can contain credentials in embedded YAML or JSON.
OMITTED_KUBE_ANNOTATIONS = {
    'kubectl.kubernetes.io/last-applied-configuration',
    'io.opentelemetry.discovery.metrics/config',
}

# Try once per scheme-like token. Trying at every letter made a long encoded
# receiver body quadratic even when it contained no URI separator.
URI_CREDENTIALS = re.compile(
    r'(?i)(?<![a-z0-9+.-])([0-9+.-]*[a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+@'
)
ASSIGNMENT_TERMS = ('password', 'passwd', 'access_token', 'refresh_token', 'authorization')
DOCUMENT_TERMS = (*ASSIGNMENT_TERMS, 'secret', 'credential', 'token', 'private_key', 'annotations')
ASSIGNMENT = re.compile(
    r'''(?P<prefix>(?:authorization["']?[ \t]*[=:][ \t]*(?:(?:bearer|basic)[ \t]+|(?!(?:bearer|basic)(?:[ \t]|$)))|'''
    r'''(?:password|passwd|access_token|refresh_token)["']?[ \t]*[=:][ \t]*))'''
    r'''(?P<value>"(?:[^"\\\r\n]|\\[^\r\n])*"|'(?:[^'\\\r\n]|\\[^\r\n])*'|'''
    r'''[^\s,;"'{}]+|["'])''',
    re.I,
)
# Locust's pinned report compares an HTML input type to a static string.
LOCUST_ASSIGNMENT = re.compile(r'(?-i:"password"===[A-Za-z_$][\w$]*\.type\b)|' + ASSIGNMENT.pattern, re.I)

MAX_ARCHIVE_BYTES = 8*1024**3
MAX_ARCHIVE_LINE = 16*1024**2
MAX_RECEIVER_FILE = 96*1024**2  # Same cap as receiver_input_proxy.
JSON_SUFFIXES = ('.json', '.ndjson', '.jsonl')
ARCHIVE_SUFFIXES = {'.gz', '.tar', '.tgz', '.zip'}


def _assignment_replacement(match):
    value = match['value']
    if value is None:  # Static Locust comparison.
        return match[0]
    if value.startswith(('"', "'")):
        if len(value) == 1:
            raise ValueError('unterminated quoted credential assignment')
        if value[1:-1] in ('', REDACTED):
            return match[0]
        return match['prefix'] + value[0] + REDACTED + value[-1]
    return match['prefix'] + REDACTED


def redact_text(text, *, locust=False, lowered=None):
    if '://' in text:
        text = URI_CREDENTIALS.sub(r'\1<redacted>@', text)
    if '=' not in text and ':' not in text:
        return text
    if lowered is None and text.isascii():
        lowered = text.lower()
    if lowered is None or any(term in lowered for term in ASSIGNMENT_TERMS):
        pattern = LOCUST_ASSIGNMENT if locust else ASSIGNMENT
        text = pattern.sub(_assignment_replacement, text)
    return text


def _project_text(text, *, locust=False):
    clean = redact_text(text, locust=locust)
    # A marker is harmless as a complete value. Removing markers also exposes
    # credential assignments that were split across a marker in the input.
    if REDACTED in clean:
        unmarked = clean.replace(REDACTED, '')
        if redact_text(unmarked, locust=locust) != unmarked:
            raise ValueError('ambiguous redaction marker in credential text')
    return clean


def redact_document(value, *, strings=None):
    """Project credential fields and optionally the decoded string values."""
    if isinstance(value, list):
        return [redact_document(item, strings=strings) for item in value]
    if isinstance(value, str) and strings is not None:
        return strings(value)
    if not isinstance(value, dict):
        return value

    annotations = value.get('annotations')
    if isinstance(annotations, dict):
        value = dict(value, annotations={key: item for key, item in annotations.items()
                                        if key not in OMITTED_KUBE_ANNOTATIONS})
    if value.get('kind') == 'Secret':
        value = dict(value, data=REDACTED, stringData=REDACTED)
    name = str(value.get('name', ''))
    sensitive_value = (
        'value' in value and (SENSITIVE.fullmatch(str(value.get('key', ''))) or SENSITIVE_ENV_NAME.search(name))
    )
    projected = {}
    for key, item in value.items():
        if strings is not None and isinstance(key, str) and strings(key) != key:
            raise ValueError('credential text in a structured evidence key')
        projected[key] = (
            REDACTED if SENSITIVE.fullmatch(str(key)) or (key == 'value' and sensitive_value)
            else redact_document(item, strings=strings)
        )
    return projected


def _redact_pod_body(body):
    projected = redact_document(body, strings=_project_text)
    for pod in projected.get('items', []) if isinstance(projected, dict) else []:
        spec = pod.get('spec', {})
        for group in ('containers', 'initContainers', 'ephemeralContainers'):
            for container in spec.get(group, []):
                for variable in container.get('env', []):
                    if 'value' in variable:
                        variable['value'] = '<redacted>'
    return projected


def project_receiver_line(line):
    if not line.strip():
        return line
    if len(line) > MAX_ARCHIVE_LINE:
        raise ValueError('oversized receiver record')
    row = _load_json(line)
    if not isinstance(row, dict) or not isinstance(row.get('path', ''), str):
        raise ValueError('invalid receiver record')
    endpoint = row.get('path', '').split('?', 1)[0].rstrip('/')
    if endpoint and 'body_base64' in row:
        try:
            original = base64.b64decode(row['body_base64'], validate=True)
            if _load_json(original) != row['body']:
                raise ValueError('parsed and encoded receiver bodies differ')
        except (KeyError, TypeError, ValueError):
            raise _EncodedReceiverError('cannot verify encoded receiver response') from None
    if endpoint != '/pods' or 'body_base64' not in row:
        if redact_document(row, strings=_project_text) != row:
            raise ValueError('unexpected credential in receiver record')
        return line
    metadata = {key: value for key, value in row.items() if key not in ('body', 'body_base64')}
    if redact_document(metadata, strings=_project_text) != metadata:
        raise ValueError('unexpected credential outside the Pod receiver body')
    body = _redact_pod_body(row['body'])
    if body == row['body']:
        return line
    projected = json.dumps(body, separators=(',', ':'), sort_keys=True).encode()
    row.update(body=body, body_base64=base64.b64encode(projected).decode(),
               body_projection={
                   'policy': 'kubelet-pods-credentials-v2',
                   'original_sha256': hashlib.sha256(original).hexdigest(),
                   'projected_sha256': hashlib.sha256(projected).hexdigest(),
               })
    return (json.dumps(row, separators=(',', ':'), sort_keys=True) + '\n').encode()


def _project_receiver_bytes(raw):
    if len(raw) > MAX_RECEIVER_FILE:
        raise ValueError('receiver capture exceeded its file bound')
    return b''.join(project_receiver_line(line) for line in raw.splitlines(keepends=True))


def _project_capture_archive(path, target):
    # Inspect the receiver member before copying the tar. Already-safe captures
    # retain their exact transport bytes and do not require rewriting every member.
    receiver = None
    replacement = None
    with tarfile.open(path, 'r:') as source:
        for member in source:
            relative = Path(member.name)
            if _unsafe_archive_path(relative, member.name) or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe collector archive member: ' + member.name)
            if member.size > MAX_ARCHIVE_BYTES:
                raise ValueError('oversized collector archive member')
            if member.isfile() and relative.name == 'receiver-input.ndjson':
                if receiver is not None:
                    raise ValueError('multiple receiver captures in one collector archive')
                receiver = member
                with source.extractfile(member) as stream:
                    raw = stream.read(MAX_RECEIVER_FILE + 1)
                clean = _project_receiver_bytes(raw)
                if clean != raw:
                    replacement = clean
        if replacement is not None:
            with tarfile.open(target, 'w') as output:
                for member in source.getmembers():
                    if member is receiver:
                        projected = copy.copy(member)
                        projected.size = len(replacement)
                        output.addfile(projected, io.BytesIO(replacement))
                    elif member.isfile():
                        with source.extractfile(member) as stream:
                            output.addfile(member, stream)
                    else:
                        output.addfile(member)
            return True
    shutil.copyfile(path, target)
    return False


def verify_archive_privacy(path):
    """Inspect compressed transport evidence without changing checksum-bearing bytes.

    Raw JSON/NDJSON archives should contain observations, not credentials. Reject
    an unsafe/opaque member rather than silently removing required source data.
    Every extracted original remains local for inspected recovery.
    """
    total=0;members=[]
    def inspect(stream,name):
        nonlocal total
        basename = Path(name).name
        while True:
            line=stream.readline(MAX_ARCHIVE_LINE+1)
            if not line:break
            if len(line) > MAX_ARCHIVE_LINE:
                raise ValueError(f'archive privacy record byte bound exceeded: {name} (limit {MAX_ARCHIVE_LINE})')
            total += len(line)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError(f'archive privacy aggregate byte bound exceeded: {path.name} at {name} (limit {MAX_ARCHIVE_BYTES})')
            try:text=line.decode()
            except UnicodeDecodeError:raise ValueError('opaque binary archive member requires inspected export: '+name)
            if '\x00' in text:
                raise ValueError('opaque binary archive member requires inspected export: ' + name)
            if not text.strip():continue
            if basename in ('receiver-input.ndjson', 'receiver-input.ndjson.gz'):
                if project_receiver_line(line) != line:
                    raise ValueError('credential-bearing Pod receiver record requires projection: ' + name)
                continue
            try:
                _verify_archive_text(text, basename)
            except (ValueError, RecursionError):
                raise ValueError('potential credential in compressed evidence; original retained: ' + name) from None
        members.append(name)
    def archive_stream(stream,name,depth=0):
        if depth>4:raise ValueError('compressed evidence nesting bound exceeded')
        if name.endswith(('.tar','.tar.gz','.tgz')):
            with tarfile.open(fileobj=stream,mode='r:*') as archive:
                for member in archive:
                    relative=Path(member.name)
                    if _unsafe_archive_path(relative, member.name) or member.issym() or member.islnk():
                        raise ValueError('unsafe archived evidence path')
                    if member.isdir():continue
                    if not member.isfile() or member.size>MAX_ARCHIVE_BYTES:raise ValueError('nonregular or oversized archived evidence member')
                    child=archive.extractfile(member)
                    if member.name.endswith(('.gz','.tar','.tgz')):
                        with tempfile.SpooledTemporaryFile(max_size=16*1024**2) as copied:
                            remaining=MAX_ARCHIVE_BYTES
                            while True:
                                chunk=child.read(min(1024**2,remaining+1))
                                if not chunk:break
                                remaining-=len(chunk)
                                if remaining<0:raise ValueError('nested archive byte bound exceeded')
                                copied.write(chunk)
                            copied.seek(0);archive_stream(copied,member.name,depth+1)
                    else:inspect(child,member.name)
        elif name.endswith('.gz'):
            with gzip.GzipFile(fileobj=stream,mode='rb') as uncompressed:inspect(uncompressed,name)
        else:raise ValueError('unsupported compressed evidence transport')
    with path.open('rb') as stream:archive_stream(stream,path.name)
    return {'path':str(path.name),'uncompressed_bytes_inspected':total,'members':members,'credential_fields_detected':False}

class _EncodedReceiverError(ValueError):
    pass


def _unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate structured evidence key')
        result[key] = value
    return result


def _reject_json_constant(_):
    raise ValueError('nonfinite value in structured evidence')


def _load_json(raw, *, sensitive=True):
    options = {'object_pairs_hook': _unique_mapping} if sensitive else {}
    return json.loads(raw, parse_constant=_reject_json_constant, **options)


def _possible_document_credentials(text, lowered):
    return (lowered is None or '://' in text or '\\u' in text
            or REDACTED in text or any(term in lowered for term in DOCUMENT_TERMS))


def _verify_archive_text(text, basename):
    lowered = text.lower() if text.isascii() else None
    if not _possible_document_credentials(text, lowered):
        return
    locust = basename == 'locust-report.html'
    structured = basename.removesuffix('.gz').endswith(JSON_SUFFIXES)
    if structured or text.lstrip().startswith(('{', '[')):
        try:
            value = _load_json(text)
        except json.JSONDecodeError:
            # Pretty JSON places an individual property on its own line.
            property_line = text.strip().removesuffix(',')
            if property_line.startswith('"'):
                try:
                    value = _load_json('{' + property_line + '}')
                except json.JSONDecodeError:
                    value = None
            else:
                value = None
        if value is not None:
            if redact_document(value, strings=_project_text) != value:
                raise ValueError('credential field in compressed evidence')
            return
    if redact_text(text, locust=locust, lowered=lowered) != text:
        raise ValueError('credential text in compressed evidence')
    if REDACTED in text:
        _project_text(text, locust=locust)


def _project_json_lines(raw):
    lines = raw.split(b'\n')
    changed = False
    for index, line in enumerate(lines):
        if len(line) + (index < len(lines) - 1) > MAX_ARCHIVE_LINE:
            raise ValueError('oversized JSON evidence record')
        if not line.strip():
            continue
        text = line.decode()
        sensitive = _possible_document_credentials(text, text.lower() if text.isascii() else None)
        value = _load_json(text, sensitive=sensitive)
        if sensitive:
            clean = redact_document(value, strings=_project_text)
            if clean != value:
                changed = True
                lines[index] = json.dumps(clean, sort_keys=True).encode()
    return b'\n'.join(lines) if changed else raw


def _project_loose_bytes(path, raw):
    if path.name == 'receiver-input.ndjson':
        return _project_receiver_bytes(raw)
    if path.suffix in ('.ndjson', '.jsonl'):
        return _project_json_lines(raw)
    text = raw.decode()
    if '\x00' in text:
        raise ValueError('opaque binary evidence')
    if path.suffix == '.json':
        sensitive = _possible_document_credentials(text, text.lower() if text.isascii() else None)
        try:
            value = _load_json(text, sensitive=sensitive)
        except json.JSONDecodeError:
            # Raw collector exports historically used NDJSON with a .json suffix.
            return _project_json_lines(raw)
        clean = redact_document(value, strings=_project_text) if sensitive else value
        return (json.dumps(clean, indent=2, sort_keys=True) + '\n').encode() if clean != value else raw
    if path.suffix in {'.yaml', '.yml'}:
        from .. import yamlio
        value = yamlio.load_all(text)
        projected = redact_document(value, strings=_project_text)
        clean = yamlio.dump_all(projected) if projected != value else text
    else:
        clean = _project_text(text, locust=path.name == 'locust-report.html')
        for line in clean.splitlines():
            _verify_archive_text(line, path.name)
    return clean.encode() if clean != text else raw


def _verify_loose_record_bounds(raw):
    # Most loose files are smaller than even one maximum archive record.
    if len(raw) > MAX_ARCHIVE_LINE:
        with io.BytesIO(raw) as stream:
            while line := stream.readline(MAX_ARCHIVE_LINE + 1):
                if len(line) > MAX_ARCHIVE_LINE:
                    raise ValueError('oversized evidence record')


def export_projection(source,destination):
    """Sealed redacted projection; originals and original hashes remain locally available."""
    source=Path(source).resolve();destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=False);changes=[];archives=[]
    for path in evidence_files(source):
        name=str(path.relative_to(source))
        if path.name in ('GKE-SHA256SUMS','EXPORT-PROJECTION.json'):continue
        target=destination/name
        target.parent.mkdir(parents=True,exist_ok=True)
        if path.suffix in ARCHIVE_SUFFIXES:
            collector_archive = ('structured' in Path(name).parts and path.name.startswith('raw-files-attempt-')
                                 and path.suffix == '.tar')
            try:
                if collector_archive:
                    changed = _project_capture_archive(path, target)
                    checked = verify_archive_privacy(target)
                    if changed:
                        changes.append({'path':name,'original_sha256':sha(path),'export_sha256':sha(target)})
                else:
                    checked = verify_archive_privacy(path)
                    shutil.copyfile(path, target)
            except (EOFError, tarfile.TarError, UnicodeError, RecursionError):
                raise ValueError('malformed evidence archive: ' + name) from None
            checked['path'] = name
            archives.append(checked)
            continue
        raw = path.read_bytes()
        try:
            clean = _project_loose_bytes(path, raw)
            _verify_loose_record_bounds(clean)
        except _EncodedReceiverError:
            raise
        except (ValueError, TypeError, RecursionError):
            raise ValueError('unsafe or malformed evidence: ' + name) from None
        target.write_bytes(clean)
        if clean!=raw:changes.append({'path':name,'original_sha256':sha(path),'export_sha256':sha(target)})
    write(destination/'EXPORT-PROJECTION.json',{'schema_version':1,'redacted_files':changes,'compressed_evidence_inspections':archives,'policy':'credential values excluded; source/status/measurement dimensions retained','local_original_seal':sha(source/'GKE-SHA256SUMS') if (source/'GKE-SHA256SUMS').exists() else None})
    return seal(destination)

def pack_window_archives(projection, destination):
    """Partition a sealed projection by recursively inspected size without splitting files."""
    projection = Path(projection).resolve()
    destination = Path(destination)
    inspections = read(projection / 'EXPORT-PROJECTION.json')['compressed_evidence_inspections']
    expanded_sizes = {entry['path']: entry['uncompressed_bytes_inspected'] for entry in inspections}
    groups = []
    group = []
    expanded_bytes = 0
    for file in evidence_files(projection):
        name = file.relative_to(projection).as_posix()
        size = max(file.stat().st_size, expanded_sizes.get(name, 0))
        if size > MAX_ARCHIVE_BYTES:
            raise ValueError(f'window archive member exceeds the privacy byte bound: {name} (limit {MAX_ARCHIVE_BYTES})')
        if group and expanded_bytes + size > MAX_ARCHIVE_BYTES:
            groups.append(group)
            group, expanded_bytes = [], 0
        group.append((file, name))
        expanded_bytes += size
    if group:
        groups.append(group)
    parts = []
    for number, members in enumerate(groups, 1):
        archive = destination / ('window.tar.gz' if number == 1 else f'window-{number:03}.tar.gz')
        with tarfile.open(archive, 'x:gz', compresslevel=1) as target:
            for file, name in members:
                target.add(file, arcname=name, recursive=False)
        parts.append({'name': archive.name, 'sha256': sha(archive), 'bytes': archive.stat().st_size})
    write(destination / 'window-index.json',
          {'schema_version': 1, 'parts': parts, 'window_seal_sha256': sha(projection / 'GKE-SHA256SUMS')})
    return parts


def upload(ctx,path,*,bucket,prefix):
    with tempfile.TemporaryDirectory(prefix='gke-publication-') as temporary:
        projection=Path(temporary)/'evidence'
        with timed(ctx,'publication-privacy',prefix):
            export_projection(path,projection)
        return _upload(ctx,projection,bucket=bucket,prefix=prefix)


def read_only_export_context(root,run_id):
    """Export an existing saved run without authorizing new infrastructure or traffic."""
    from .teardown import load_cleanup_context
    ctx=load_cleanup_context(root,run_id)
    bucket=ctx.config['gcp']['project_id']+'-karthos-eval-evidence'
    def command(args,*,timeout=300,check=True,**ignored):
        args=[str(arg) for arg in args]
        if not (args[:3]==['gcloud','storage','cp'] or args[:4]==['gcloud','storage','objects','describe']):raise ValueError('export context permits storage export/readback only')
        uris=[arg for arg in args if arg.startswith('gs://')]
        if len(uris)!=1 or not uris[0].startswith('gs://'+bucket+'/gke-qualification-v1/runs/'+run_id+'/exports/'):raise ValueError('export target differs from saved run')
        value=ctx.executor(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
        if check and value.returncode:raise RuntimeError('read-only run export failed: '+value.stderr[-500:])
        return value
    ctx.command=command
    return ctx
