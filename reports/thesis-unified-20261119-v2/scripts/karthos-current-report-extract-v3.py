import sys,json,hashlib,tarfile
from pathlib import Path
from evaluation.gke.analysis_reader import read_analysis_report
archive=Path(sys.argv[1]);expected=sys.argv[2];output=Path(sys.argv[3])
if output.exists():raise ValueError('output already exists')
h=hashlib.sha256()
with archive.open('rb') as stream:
 for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
if h.hexdigest()!=expected:raise ValueError('archive checksum mismatch')
with tarfile.open(archive) as tar:
 members=tar.getmembers();names=[m.name for m in members]
 if len(set(names))!=len(names) or any(not m.isfile() or Path(m.name).is_absolute() or '..' in Path(m.name).parts for m in members):raise ValueError('unsafe archive')
 if sum(m.size for m in members)>2*1024**3:raise ValueError('analysis expansion exceeds 2GiB')
 seal=tar.extractfile('GKE-SHA256SUMS').read();seals={n:s for s,n in (line.split('  ',1) for line in seal.decode().splitlines())}
 if set(names)!=(set(seals)|{'GKE-SHA256SUMS'}):raise ValueError('seal population mismatch')
 checked={}
 for m in members:
  if m.name=='GKE-SHA256SUMS':continue
  h=hashlib.sha256()
  with tar.extractfile(m) as f:
   for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
  if h.hexdigest()!=seals[m.name]:raise ValueError('member checksum mismatch '+m.name)
  checked[m.name]={'bytes':m.size,'sha256':h.hexdigest()}
 print(json.dumps({'stage':'archive-verified','members':len(checked)}),flush=True)
 report,size=read_analysis_report(tar.extractfile('gke-evaluation.json'),seals['gke-evaluation.json'],open_member=tar.extractfile)
 print(json.dumps({'stage':'report-decoded','keys':list(report)}),flush=True)
 keys=('spec','execution','evidence','manifest','source_selection','structural_fidelity','background_traffic','settled_trace_population','settled_duration_diagnostics','families','evidence_horizon','thesis_answers','single_contribution','resources','selected_accuracy','numerical_agreement','thresholds','analysis','service_graph_evidence','boundary_summary','product_revision','verifier_version','verifier_implementation_hashes','protocol','schema_version','input_population','projected_input_proof','reference_inventory','references','limitations','comparison_inputs')
 projected={k:report[k] for k in keys if k in report}
 sizes={k:len(json.dumps(v)) for k,v in projected.items()};print(json.dumps({'stage':'field-sizes','bytes':sizes}),flush=True)
 result={'extraction_version':'report-projection-v3','operational_note':'128MiB output bound; metric_window_coverage stays indexed in sealed source archive rather than duplicated; scientific results and archive verification unchanged','input_archive':{'name':archive.name,'sha256':expected,'bytes':archive.stat().st_size},'seal_sha256':hashlib.sha256(seal).hexdigest(),'verified_members':checked,'projection':'existing analysis_reader; omit duplicated matching-reference payloads and selected_accuracy checks; source archive remains authoritative; no rescoring','report':projected}
 encoded=json.dumps(result,indent=2,sort_keys=True)+'\n'
 if len(encoded.encode())>128*1024**2:raise ValueError('projection exceeds128MiB')
 output.parent.mkdir(parents=True,exist_ok=True)
 with output.open('x') as f:f.write(encoded)
 print(json.dumps({'stage':'saved','bytes':len(encoded.encode()),'path':str(output)}),flush=True)
