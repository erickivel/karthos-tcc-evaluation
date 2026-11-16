"""Lossless compact reports with checksum-addressed JSONL evidence.

Ordered reference occurrences remain in the report/comparison, including duplicates
and conflicting identities. Only byte-identical canonical payloads share storage.
The root is published last; a failed write cannot replace an older report.
Analysis validates *all* sidecar bytes even when matching payloads are omitted.
"""
from __future__ import annotations
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import uuid
from .models import write, sha

FORMAT='gke-compact-report-v1'
MARKER='$compact_ref'
LITERAL='$compact_literal'
MAX_RECORD_BYTES=16*1024**2  # Matches the export privacy JSONL record bound.
ENCODER=json.JSONEncoder(sort_keys=True,separators=(',',':'),ensure_ascii=True)


def _chunks(value):
    for chunk in ENCODER.iterencode(value):
        yield chunk.encode('utf-8')


class _Store:
    def __init__(self,path,kind):
        self.path=path;self.kind=kind;self.stream=path.open('xb')
        self.digests={};self.identities={}

    def add(self,value,*,identity=None):
        if identity is not None and identity in self.identities:
            return self.identities[identity]
        digest=hashlib.sha256()
        size=0
        for chunk in _chunks(value):
            size+=len(chunk)
            if size+1>MAX_RECORD_BYTES:raise ValueError('compact record exceeds explicit JSONL size bound')
            digest.update(chunk)
        key=digest.hexdigest()
        if key not in self.digests:
            offset=self.stream.tell()
            for chunk in _chunks(value):self.stream.write(chunk)
            length=self.stream.tell()-offset
            self.stream.write(b'\n')
            self.digests[key]={MARKER:{'kind':self.kind,'sha256':key,'offset':offset,'bytes':length}}
        ref=self.digests[key]
        if identity is not None:self.identities[identity]=ref
        return ref

    def manifest(self):
        self.stream.close()
        return {'name':self.path.name,'sha256':sha(self.path),'bytes':self.path.stat().st_size,'records':len(self.digests)}


def write_report(path,report):
    """Write lossless evidence; return root with checks replaced by ordered refs.

    Does not mutate report. Call save_summary(path.parent, report) afterward while
    the original checks are still available to its existing verdict calculation.
    """
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if 'compact_report' in report:raise ValueError('report is already compact')
    token=uuid.uuid4().hex
    stores={kind:_Store(path.with_name(path.stem+'.'+token+'.'+kind+'.jsonl'),kind)
            for kind in ('references','reference_sets','comparisons')}
    published=False
    try:
        def transform(value):
            if isinstance(value,dict):
                result={}
                for key,item in value.items():
                    if key=='matching_references' and isinstance(item,list):
                        cached=stores['reference_sets'].identities.get(id(item))
                        result[key]=cached if cached is not None else stores['reference_sets'].add(
                            [reference(row) for row in item],identity=id(item))
                    elif key=='checks' and isinstance(item,list):
                        result[key]=[comparison(row) for row in item]
                    else:result[key]=transform(item)
                if set(result) in ({MARKER},{LITERAL}):return {LITERAL:list(result.items())}
                return result
            if isinstance(value,list):return [transform(item) for item in value]
            return value
        def reference(row):
            cached=stores['references'].identities.get(id(row))
            if cached is not None:return cached
            return stores['references'].add(transform(row),identity=id(row))
        def comparison(row):
            cached=stores['comparisons'].identities.get(id(row))
            if cached is not None:return cached
            return stores['comparisons'].add(transform(row),identity=id(row))
        result=transform(report)
        result['compact_report']={'format':FORMAT,'members':{kind:store.manifest() for kind,store in stores.items()}}
        write(path,result)
        published=True
        return result
    finally:
        for store in stores.values():
            store.stream.close()
            if not published:store.path.unlink(missing_ok=True)


def _safe_member(name):
    if not isinstance(name,str) or Path(name).name!=name or name in ('','.','..'):
        raise ValueError('unsafe compact report member')
    return name


class _Reader:
    def __init__(self,root,open_member,stack,input_members=None):
        meta=root.get('compact_report',{})
        if meta.get('format')!=FORMAT:raise ValueError('unsupported compact report format')
        self.streams={};self.members=meta['members']
        if set(self.members)!={'references','reference_sets','comparisons'}:raise ValueError('compact report members differ')
        for kind,entry in self.members.items():
            name=_safe_member(entry['name'])
            stream=stack.enter_context(open_member(name));digest=hashlib.sha256();size=0
            while True:
                data=stream.read(1024*1024)
                if not data:break
                digest.update(data);size+=len(data)
            if digest.hexdigest()!=entry['sha256'] or size!=entry['bytes']:
                raise ValueError('compact report member checksum differs: '+name)
            stream.seek(0);self.streams[kind]=stream
            if input_members is not None:input_members[name]={'sha256':entry['sha256'],'bytes':size}

    def resolve(self,value,projection=False,prefix=''):
        if isinstance(value,dict):
            if set(value)=={LITERAL}:
                return {key:self.resolve(item,projection,prefix+'.'+key if prefix else key) for key,item in value[LITERAL]}
            if set(value)=={MARKER}:
                ref=value[MARKER];kind=ref['kind'];stream=self.streams[kind]
                offset=ref['offset'];size=ref['bytes']
                if type(offset) is not int or type(size) is not int or offset<0 or size<0 or offset+size>self.members[kind]['bytes'] or size+1>MAX_RECORD_BYTES:
                    raise ValueError('invalid compact record extent')
                stream.seek(offset);raw=stream.read(size)
                if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('compact record checksum differs')
                return self.resolve(json.loads(raw),projection,prefix)
            result={}
            for key,item in value.items():
                child=prefix+'.'+key if prefix else key
                if key=='compact_report' and not prefix:continue
                if projection and (key=='matching_references' or child in ('selected_accuracy.item.checks','traffic.report.availability.observations')):continue
                result[key]=self.resolve(item,projection,child)
            return result
        if isinstance(value,list):return [self.resolve(item,projection,prefix+'.item') for item in value]
        return value


def expand_report(root,open_member,*,projection=True,input_members=None):
    """Verify external members then rebuild full data or the legacy analysis view."""
    if open_member is None:raise ValueError('compact report requires external member reader')
    with ExitStack() as stack:
        reader=_Reader(root,open_member,stack,input_members)
        return reader.resolve(root,projection)


def read_report(path,expected_sha256=None,*,projection=True):
    path=Path(path)
    if expected_sha256 is not None and sha(path)!=expected_sha256:raise ValueError('report checksum differs')
    if projection:
        from .analysis_reader import read_analysis_report
        with path.open('rb') as stream:
            result,_=read_analysis_report(stream,expected_sha256 or sha(path),open_member=lambda name:(path.parent/name).open('rb'))
        return result
    with path.open() as stream:root=json.load(stream)
    if 'compact_report' not in root:return root
    return expand_report(root,lambda name:(path.parent/name).open('rb'),projection=False)


def iter_comparisons(path,*,resolve_references=True):
    """Yield each stored comparison once; root references retain occurrence order."""
    import ijson
    path=Path(path)
    metadata=None
    try:
        with path.open('rb') as stream:
            for item in ijson.items(stream,'compact_report',use_float=True):
                if metadata is not None:raise ValueError('duplicate compact report metadata')
                metadata=item
    except ijson.JSONError as exc:raise ValueError('invalid compact report JSON') from exc
    if metadata is None:
        with path.open('rb') as stream:
            yield from ijson.items(stream,'numerical_agreement.checks.item',use_float=True)
        return
    with ExitStack() as stack:
        reader=_Reader({'compact_report':metadata},lambda name:(path.parent/name).open('rb'),stack)
        stream=reader.streams['comparisons']
        offset=0
        while offset<reader.members['comparisons']['bytes']:
            stream.seek(offset);raw=stream.readline(MAX_RECORD_BYTES+1);offset=stream.tell()
            if len(raw)>MAX_RECORD_BYTES:raise ValueError('compact record exceeds explicit JSONL size bound')
            yield reader.resolve(json.loads(raw),projection=not resolve_references)
