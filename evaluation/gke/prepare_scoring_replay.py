"""Prepare a new offline scoring workspace from a verified projected raw archive.

No network or cloud operations. The projected population is never represented as
byte-identical original capture. Original evidence and both source versions remain
bound by hashes. Single-part raw archives only; multipart inputs fail closed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
from .models import sha,write,canonical_sha,implementation_hashes
from .archive_inputs import _inventory

PIPELINE='sealed-before-analysis-v1'
DOCUMENT_LIMIT=8*1024**2
ARCHIVE_LIMIT=8*1024**3


def _document(path,digest):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>DOCUMENT_LIMIT or sha(path)!=digest:
        raise ValueError('document checksum, type or size differs: '+str(path))
    with path.open() as stream:value=json.load(stream)
    if not isinstance(value,dict):raise ValueError('binding document must be an object')
    return value


def _manifest(path,digest):
    value=_document(path,digest)
    required={'schedule','thresholds','product_pins','protocol_definition','implementation_hashes','implementation_sha256','config'}
    if not required<=set(value):raise ValueError('measurement manifest fields missing')
    if not isinstance(value['schedule'],str) or not value['schedule'] or any(not isinstance(value[k],dict) for k in required-{'schedule','implementation_sha256'}):
        raise ValueError('measurement manifest field types differ')
    if value['implementation_sha256']!=canonical_sha(value['implementation_hashes']):raise ValueError('measurement implementation hash differs')
    if value['config'].get('evidence_pipeline')!=PIPELINE:raise ValueError('measurement manifest lacks evidence-first producer contract')
    return value


def _receipt(path,digest,archive_sha256):
    value=_document(path,digest)
    if value.get('verified') is not True or value.get('archive_sha256')!=archive_sha256 or value.get('archive_parts')!=1:
        raise ValueError('receipt does not verify this single raw archive')
    if not isinstance(value.get('original_window_seal_sha256'),str) or len(value['original_window_seal_sha256'])!=64:
        raise ValueError('receipt original seal identity missing')
    return value


def _archive(archive,digest):
    archive=Path(archive)
    if archive.is_symlink() or sha(archive)!=digest:raise ValueError('raw archive checksum differs')
    return tarfile.open(archive,'r:*')


def _population(tar,receipt):
    members,seals=_inventory(tar)
    if sum(member.size for member in members.values())>ARCHIVE_LIMIT:raise ValueError('projected archive exceeds supported size')
    if any(name=='analysis' or name.startswith('analysis/') for name in members):raise ValueError('raw archive unexpectedly contains analysis outputs')
    def document(name):
        if name not in members or members[name].size>DOCUMENT_LIMIT:raise ValueError('missing or oversized projected binding '+name)
        data=tar.extractfile(name).read()
        if hashlib.sha256(data).hexdigest()!=seals.get(name):raise ValueError('projected binding checksum differs: '+name)
        return data
    original=document('LOCAL-ORIGINAL-GKE-SHA256SUMS')
    if hashlib.sha256(original).hexdigest()!=receipt['original_window_seal_sha256']:raise ValueError('original seal chain differs')
    projection=json.loads(document('EXPORT-PROJECTION.json'))
    if projection.get('local_original_seal')!=receipt['original_window_seal_sha256']:raise ValueError('privacy projection original seal chain differs')
    context=json.loads(document('scoring-context.json'))
    seal=tar.extractfile('GKE-SHA256SUMS').read()
    return members,seals,hashlib.sha256(seal).hexdigest(),context


def _producer(context,manifest):
    if context.get('manifest_sha256')!=canonical_sha(manifest):raise ValueError('measurement producer manifest hash differs or is missing')
    if context.get('protocol')!=manifest['schedule'] or context.get('evidence_pipeline')!=PIPELINE:
        raise ValueError('measurement producer protocol or evidence pipeline differs')


def verify_projection_inputs(window,receipt,proof):
    """Re-verify archived authority and every projected input before/after scoring."""
    if proof.get('schema_version')!=1 or proof.get('input_population')!='verified-projection':raise ValueError('unsupported projected input proof')
    expected_receipt=_receipt(proof['receipt'],proof['receipt_sha256'],proof['archive_sha256'])
    if expected_receipt!=receipt:raise ValueError('worker receipt differs from projected input proof')
    manifest=_manifest(proof['manifest'],proof['manifest_sha256'])
    source_hashes=implementation_hashes(proof['source_root'])
    if source_hashes!=proof['verifier_implementation_hashes'] or canonical_sha(source_hashes)!=proof['verifier_implementation_sha256']:
        raise ValueError('replay verifier implementation differs')
    window=Path(window).resolve()
    with _archive(proof['archive'],proof['archive_sha256']) as tar:
        members,seals,seal_digest,context=_population(tar,receipt);_producer(context,manifest)
        if seal_digest!=proof['projected_seal_sha256'] or sha(window/'GKE-SHA256SUMS')!=seal_digest:
            raise ValueError('projected local seal is not the verified archive seal')
        if proof['original_window_seal_sha256']!=receipt['original_window_seal_sha256']:raise ValueError('proof original seal differs')
        for name,digest in seals.items():
            member=window/name
            if member.is_symlink() or not member.resolve().is_relative_to(window) or not member.is_file() or sha(member)!=digest:
                raise ValueError('projected input checksum differs: '+name)
        for member in window.rglob('*'):
            name=member.relative_to(window).as_posix()
            if member.is_symlink():raise ValueError('symlink in projected scoring input')
            if member.is_file() and name not in members and not name.startswith('analysis/'):
                raise ValueError('unsealed projected scoring input: '+name)
    return {'verified':True,'members':len(seals),'input_population':'verified-projection','original_byte_equivalence':False}


def prepare(archive,destination,*,archive_sha256,receipt_path,receipt_sha256,manifest_path,manifest_sha256,source_root,verifier_sha256):
    """Extract once to a new directory and return a hash-bound worker input path."""
    archive=Path(archive).resolve();destination=Path(destination).absolute();source_root=Path(source_root).resolve()
    if destination.exists() or destination.is_symlink():raise FileExistsError(destination)
    if destination.resolve().is_relative_to(source_root):raise ValueError('replay workspace must be outside verifier source tree')
    receipt=_receipt(receipt_path,receipt_sha256,archive_sha256);manifest=_manifest(manifest_path,manifest_sha256)
    source_hashes=implementation_hashes(source_root)
    if canonical_sha(source_hashes)!=verifier_sha256:raise ValueError('reviewed verifier source checksum differs')
    created=False
    try:
        with _archive(archive,archive_sha256) as tar:
            members,seals,seal_digest,context=_population(tar,receipt);_producer(context,manifest)
            destination.mkdir(mode=0o700,parents=True,exist_ok=False);created=True
            window=destination/'window';window.mkdir(mode=0o700)
            for name,member in members.items():
                target=window/name
                if member.isdir():target.mkdir(parents=True,exist_ok=True);continue
                target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as stream,target.open('xb') as output:
                    shutil.copyfileobj(stream,output,1024*1024)
                if name!='GKE-SHA256SUMS' and sha(target)!=seals[name]:raise ValueError('archive member checksum differs: '+name)
        retained_receipt=destination/'raw-export-receipt.json';retained_manifest=destination/'measurement-manifest.json'
        shutil.copyfile(receipt_path,retained_receipt);shutil.copyfile(manifest_path,retained_manifest)
        proof={'schema_version':1,'input_population':'verified-projection','archive':str(archive),'archive_sha256':archive_sha256,
               'receipt':str(retained_receipt),'receipt_sha256':receipt_sha256,'manifest':str(retained_manifest),'manifest_sha256':manifest_sha256,
               'projected_seal_sha256':seal_digest,'original_window_seal_sha256':receipt['original_window_seal_sha256'],
               'source_root':str(source_root),'verifier_implementation_hashes':source_hashes,'verifier_implementation_sha256':verifier_sha256}
        verify_projection_inputs(window,receipt,proof)
        binding={'raw_evidence_export':receipt,'manifest':manifest,'source_root':str(source_root),'window':str(window),
                 'input_population':'verified-projection','projected_input_proof':proof,
                 'verifier_implementation_hashes':source_hashes,'verifier_implementation_sha256':verifier_sha256,
                 'original_byte_equivalence':False,'measurement_implementation_sha256':manifest['implementation_sha256']}
        path=window/'analysis/input-binding.json';write(path,binding)
        # The root is private throughout preparation; normalize every child
        # explicitly rather than inheriting a possibly permissive operator umask.
        for member in destination.rglob('*'):member.chmod(0o700 if member.is_dir() else 0o600)
        return path
    except BaseException:
        if created:shutil.rmtree(destination)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path);parser.add_argument('destination',type=Path)
    for name in ('archive-sha256','receipt-sha256','manifest-sha256','verifier-sha256'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--receipt',required=True,type=Path);parser.add_argument('--manifest',required=True,type=Path);parser.add_argument('--source-root',required=True,type=Path)
    args=parser.parse_args()
    path=prepare(args.archive,args.destination,archive_sha256=args.archive_sha256,receipt_path=args.receipt,receipt_sha256=args.receipt_sha256,
                 manifest_path=args.manifest,manifest_sha256=args.manifest_sha256,source_root=args.source_root,verifier_sha256=args.verifier_sha256)
    print(json.dumps({'input_binding':str(path),'input_population':'verified-projection','original_byte_equivalence':False}))

if __name__=='__main__':main()
