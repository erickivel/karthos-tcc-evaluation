"""Read-only view of separately sealed raw and analysis archives; no extraction.

Only declared analysis outputs may shadow preliminary orchestration documents.
Raw telemetry always comes from the raw bundle. Both physical archive identities
and every selected member's origin remain available to analysis manifests.
"""
from contextlib import contextmanager,ExitStack
import hashlib
import io
import json
from pathlib import Path,PurePosixPath
import re
import tarfile
from .models import sha

ANALYSIS_FILES={'gke-evaluation.json','orchestration-summary.json','boundary-summary.html',
    'input-binding.json','analysis-status.json','worker-status.json','worker.stdout','worker.stderr',
    'usage.json','transition.json','diagnostic-cleanup.json','EXPORT-PROJECTION.json','GKE-SHA256SUMS'}
SHADOW={'gke-evaluation.json','orchestration-summary.json','boundary-summary.html','EXPORT-PROJECTION.json'}
SIDECARE=re.compile(r'gke-evaluation\.[a-f0-9]{32}\.(references|reference_sets|comparisons)\.jsonl')


def analysis_status_state(status,worker=None):
    """A partial report cannot overrule the parent's terminal worker outcome."""
    state=status.get('analysis',{}).get('state')
    if state=='failed':return state
    if state!='complete':raise ValueError('authoritative analysis status is unavailable or nonterminal')
    if not isinstance(worker,dict) or worker.get('state')!='complete' or worker.get('workers_stopped') is not True or worker.get('returncode')!=0:
        raise ValueError('complete analysis requires a successful stopped worker')
    return state


def _inventory(tar):
    members={}
    for member in tar.getmembers():
        name=PurePosixPath(member.name)
        if member.name in members or name.is_absolute() or '..' in name.parts or str(name)!=member.name or not (member.isfile() or member.isdir()):
            raise ValueError('unsafe or duplicate archive member')
        members[member.name]=member
    seals={}
    for line in tar.extractfile('GKE-SHA256SUMS').read().decode().splitlines():
        digest,name=line.split('  ',1)
        if name in seals or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('invalid archive seal')
        seals[name]=digest
    actual={name for name,member in members.items() if member.isfile() and name!='GKE-SHA256SUMS'}
    if actual!=set(seals):raise ValueError('archive seal population differs')
    return members,seals


class ArchiveView:
    def __init__(self,raw,analysis=None,*,raw_sha256=None):
        self.raw_receipt=None;self.analysis_state=None
        self.tars={'raw':raw};members,seals=_inventory(raw)
        self.members=dict(members);self.seals=dict(seals);self.origins={name:'raw' for name in members}
        if analysis is not None:
            late,late_seals=_inventory(analysis);self.tars['analysis']=analysis
            for name,member in late.items():
                if member.isdir():continue
                if name not in ANALYSIS_FILES and not SIDECARE.fullmatch(name):raise ValueError('unexpected analysis member: '+name)
                if name in members and name not in SHADOW and name!='GKE-SHA256SUMS':
                    if seals.get(name)!=late_seals.get(name):raise ValueError('conflicting archive member: '+name)
                self.members[name]=member;self.origins[name]='analysis'
                if name!='GKE-SHA256SUMS':self.seals[name]=late_seals[name]
            binding=late.get('input-binding.json')
            if binding is None or binding.size>8*1024**2:raise ValueError('missing or oversized analysis input binding')
            payload=analysis.extractfile(binding).read()
            if hashlib.sha256(payload).hexdigest()!=late_seals['input-binding.json']:raise ValueError('analysis binding checksum differs')
            receipt=json.loads(payload).get('raw_evidence_export',{})
            # These APIs accept a single raw archive. Multipart exports require
            # an explicit multi-part reader, never an incomplete first part.
            if receipt.get('verified') is not True or receipt.get('archive_sha256')!=raw_sha256:
                raise ValueError('analysis does not bind this single raw archive checksum')
            self.raw_receipt=receipt
            def document(name,required=True):
                member=late.get(name)
                if member is None:
                    if required:raise ValueError('authoritative analysis status unavailable: '+name)
                    return None
                if member.size>8*1024**2:raise ValueError('oversized analysis status')
                raw=analysis.extractfile(member).read()
                if hashlib.sha256(raw).hexdigest()!=late_seals[name]:raise ValueError('analysis status checksum differs')
                return json.loads(raw)
            status=document('analysis-status.json')
            if status.get('raw_evidence_export')!=receipt:raise ValueError('analysis status raw-evidence binding differs')
            self.analysis_state=analysis_status_state(status,document('worker-status.json',required=False))
        seal=''.join(digest+'  '+name+'\n' for name,digest in self.seals.items()).encode()
        self.virtual_seal=seal;info=tarfile.TarInfo('GKE-SHA256SUMS');info.size=len(seal)
        self.members['GKE-SHA256SUMS']=info;self.origins['GKE-SHA256SUMS']='virtual-view'

    def validate_report(self,report):
        if self.analysis_state=='complete' and report.get('analysis',{}).get('state')!='complete':
            raise ValueError('report and authoritative analysis status differ')
        if self.raw_receipt is not None and report.get('raw_evidence_export')!=self.raw_receipt:
            raise ValueError('final report raw-evidence binding differs from analysis input binding')

    def getmembers(self):return list(self.members.values())
    def getnames(self):return list(self.members)
    def getmember(self,name):return self.members[name]
    def extractfile(self,member):
        name=member if isinstance(member,str) else member.name
        if name=='GKE-SHA256SUMS':return io.BytesIO(self.virtual_seal)
        if name=='gke-evaluation.json' and self.analysis_state=='failed':
            raise ValueError('authoritative analysis failed; partial report is diagnostic only')
        return self.tars[self.origins[name]].extractfile(self.members[name])


@contextmanager
def open_archives(archive,expected_sha256,*,analysis_archive=None,analysis_sha256=None):
    if (analysis_archive is None)!=(analysis_sha256 is None):raise ValueError('analysis archive and checksum must be supplied together')
    paths=[(Path(archive),expected_sha256)]
    if analysis_archive is not None:paths.append((Path(analysis_archive),analysis_sha256))
    for path,digest in paths:
        if sha(path)!=digest:raise ValueError('archive checksum differs: '+str(path))
    with ExitStack() as stack:
        raw=stack.enter_context(tarfile.open(paths[0][0],'r:*'))
        late=stack.enter_context(tarfile.open(paths[1][0],'r:*')) if len(paths)>1 else None
        yield ArchiveView(raw,late,raw_sha256=expected_sha256)
    for path,digest in paths:
        if sha(path)!=digest:raise ValueError('archive changed during analysis: '+str(path))
