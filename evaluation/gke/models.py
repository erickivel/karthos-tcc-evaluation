"""Immutable experiment definitions; JSON-compatible YAML needs no runtime parser dependency."""
from __future__ import annotations
import hashlib,json,re
from pathlib import Path

PROTOCOL='gke-qualification-v1'
QUALIFICATION='qualification'
REHEARSAL='diagnostic-rehearsal'
THESIS_V2='thesis-qualification-v2'
THESIS='thesis-qualification-v3'
THESIS_SCHEDULES=(THESIS_V2,THESIS)
THESIS_HISTORY_SECONDS=720
PHASES=('smoke','baseline','resource','arbitration','restart')

def read(path):return json.loads(Path(path).read_text())
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value if isinstance(value,str) else json.dumps(value,indent=2,sort_keys=True)+'\n')
def sha(path):
    with Path(path).open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()
def canonical_sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def identifier(value):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,47}',value):raise ValueError('identifier must be lowercase letters/digits/hyphens, starting with a letter, <=48 characters')
    return value

def validate_config(c):
    if c.get('schema_version')!=1 or c.get('protocol')!=PROTOCOL:raise ValueError('unsupported configuration version/protocol')
    i=c['infrastructure'];l=c['limits'];g=c['gcp']
    if i['worker_count']!=3 or i['worker_machine_type']!='e2-standard-4' or i['controller_machine_type']!='e2-standard-4':raise ValueError('frozen infrastructure requires three fixed e2-standard-4 workers and controller')
    reader_cpu = i.get('gateway_reader_cpu_limit_m', 750)
    if type(reader_cpu) is not int or not 100 <= reader_cpu <= 1000:
        raise ValueError('gateway_reader_cpu_limit_m must be an integer between 100 and 1000')
    if g['region']!='us-central1' or g['zone'] not in ('us-central1-a','us-central1-b'):raise ValueError('frozen region/zone changed')
    if l['gross_limit_usd']!=100 or l['launch_ceiling_usd']!=80 or l['infrastructure_seconds']!=43200 or l['diagnostic_hold_seconds']!=1800 or l['raw_evidence_retention_days']!=90:raise ValueError('frozen budget/deadline/retention changed')
    for key,image in i.get('extra_image_lock',{}).items():
        if not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',str(image)):raise ValueError('extra image lock must contain immutable digests: '+key)
    return c

def build_matrix(p, *, schedule=QUALIFICATION):
    if p['id']!=(schedule if schedule in THESIS_SCHEDULES else PROTOCOL) or p['repetitions']!=2 or not p['rebuild_cluster']:raise ValueError('invalid frozen protocol')
    if p['baseline_rate']!=2 or p['smoke_seconds']!=60 or p['baseline_seconds']!=300 or p['diagnostic_seconds']!=300:raise ValueError('frozen workloads or duration changed')
    rows=[]
    def add(rep,phase,demo,source,generator,scenario,seconds):
        cell=f'{demo}-{source}';key=f'r{rep}-{phase}-{cell}-{generator}-{scenario}'
        rows.append(dict(id=key,repeat=rep,phase=phase,cell=cell,demo=demo,source=source,generator=generator,scenario=scenario,duration_seconds=seconds,rate=p['baseline_rate'],execution='serial'))
    if schedule in THESIS_SCHEDULES:
        if schedule == THESIS and (p.get('generators') != ['k6']
                or p.get('readiness_definition_version') != 3
                or p.get('readiness_cases') != 13
                or p.get('readiness_load_seconds') != 1020):
            raise ValueError('active thesis requires the versioned k6-only readiness contract')
        groups=p.get('cluster_cell_order')
        if groups!=[['boutique-trace','astronomy-trace','astronomy-mesh','boutique-mesh'],
                    ['boutique-mesh','boutique-trace']] or p.get('measured_generators')!=['k6']:
            raise ValueError('invalid frozen thesis cells or measured generator')
        generator,=p['measured_generators']
        for rep,ordered in enumerate(groups,1):
            for cell in ordered:
                demo,source=cell.split('-',1)
                for phase in ('smoke','baseline'):
                    add(rep,phase,demo,source,generator,'matched',p[phase+'_seconds'])
        if p.get('history_observation_seconds')!=THESIS_HISTORY_SECONDS:
            raise ValueError('thesis history observation duration changed')
        return rows
    for rep in (1,2):
        for phase in ('smoke','baseline'):
            for n,(demo,source) in enumerate((('boutique','trace'),('astronomy','trace'),('boutique','mesh'),('astronomy','mesh'))):
                gens=['k6','locust'] if (n+rep)%2 else ['locust','k6']
                for gen in gens:add(rep,phase,demo,source,gen,'matched',p[phase+'_seconds'])
        for scenario in p['resource_scenarios']:add(rep,'resource','boutique','trace','k6',scenario,300)
        for scenario in p['arbitration_scenarios']:add(rep,'arbitration','boutique','mesh' if scenario=='both-sources' else 'trace','k6',scenario,300)
    for source in ('trace','mesh'):
        for scenario in p['restart_scenarios']:add(2,'restart','boutique',source,'k6',scenario,300)
    if len(rows)!=46 or sum(x['duration_seconds'] for x in rows)!=9960:raise ValueError('matrix is not the agreed 46 windows / 166 minutes')
    if schedule==QUALIFICATION:return rows
    if schedule!=REHEARSAL:raise ValueError('unknown GKE schedule: '+str(schedule))
    baseline_cases={('astronomy-trace','k6'),('boutique-mesh','locust')}
    rehearsed=[r for r in rows if r['repeat']==1 and (
        r['phase']=='smoke' and (r['cell']!='boutique-trace' and (r['cell'],r['generator'])!=('astronomy-trace','locust'))
        or r['phase']=='baseline' and (r['cell'],r['generator']) in baseline_cases
        or r['phase'] in ('resource','arbitration')
    )]
    rehearsed.extend(dict(r,repeat=1,id='r1-'+r['id'][3:]) for r in rows if r['phase']=='restart')
    smokes=set()
    for row in rehearsed:
        key=(row['repeat'],row['cell'],row['generator'])
        if row['phase']=='smoke':smokes.add(key)
        if row['phase']=='baseline' and key not in smokes:raise ValueError('rehearsal baseline requires an earlier matching smoke')
    if len(rehearsed)!=16 or sum(r['duration_seconds'] for r in rehearsed)!=3600:
        raise ValueError('rehearsal requires 16 windows / 60 scheduled minutes')
    return rehearsed

def validate_schedule(manifest):
    schedule=manifest.get('schedule',QUALIFICATION)
    expected=build_matrix(manifest['protocol_definition'],schedule=schedule)
    if manifest['matrix']!=expected or len({r['id'] for r in expected})!=len(expected):
        raise ValueError('execution requires the exact frozen '+str(len(expected))+'-window matrix')
    if schedule in (REHEARSAL,*THESIS_SCHEDULES) and manifest.get('verification_observation'):
        raise ValueError('the diagnostic rehearsal uses schedule completion, without an observation cutoff')
    return schedule


def definition_path(ctx,name):
    schedule=getattr(ctx,'manifest',{}).get('schedule')
    protocol=schedule if schedule in THESIS_SCHEDULES else PROTOCOL
    return Path(ctx.root)/'protocols'/protocol/name


def implementation_hashes(root):
    root=Path(root);paths=[]
    for prefix in ['evaluation','infrastructure','protocols','config','docs']:
        base=root/prefix
        if base.exists():
            paths.extend(p for p in base.rglob('*') if p.is_file() and str(p.relative_to(root))!='config/gke.local.yaml' and not p.is_symlink() and not set(p.relative_to(root).parts)&{'__pycache__','.terraform','.git'} and p.suffix not in {'.pyc','.tfstate','.tfplan'} and not p.name.endswith('.backup'))
    for name in ['gke-eval.sh','README.md']:
        if (root/name).exists():paths.append(root/name)
    return {str(p.relative_to(root)):sha(p) for p in sorted(set(paths))}
def implementation_fingerprint(root):return canonical_sha(implementation_hashes(root))

def classify_readiness(c,artifact_checks):
    issues=[];g=c['gcp'];i=c['infrastructure']
    if not g.get('project_id'):issues.append('project_id must be supplied; no project has been created')
    if not g.get('billing_account_id'):issues.append('billing account linkage is not verified')
    for k in ['billing_and_credit_verified','quotas_verified','pricing_verified','toolchain_verified','capacity_verified','secondary_export_verified']:
        if not c['readiness'].get(k):issues.append(k+' is pending')
    if not i.get('kubernetes_version'):issues.append('exact supported GKE version is pending')
    for k in ['controller_image','watchdog_image','bootstrap_sdk_image']:
        if not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',str(i.get(k,''))):issues.append(k+' needs a reviewed immutable digest')
    if not i.get('controller_boot_image') or '/images/' not in i['controller_boot_image'] or '/family/' in i['controller_boot_image']:issues.append('immutable controller boot image is pending')
    if not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',str(c['readiness'].get('resource_helper_image',''))):issues.append('resource helper image digest is pending')
    if not c['readiness'].get('secondary_export_destination'):issues.append('secondary export destination is pending')
    extra=i.get('extra_image_lock',{})
    if not extra.get('docker.io/istio/pilot:1.28.2'):issues.append('Istio 1.28.2 pilot immutable digest is pending')
    mirrors=i.get('product_image_mirrors',{})
    for name in ['karthos-backend','karthos-frontend']:
        if not re.fullmatch(r'[^\s]+@sha256:[a-f0-9]{64}',str(mirrors.get(name,''))):issues.append(name+' reachable immutable mirror is pending')
    for row in artifact_checks:
        if row.get('state')!='verified':issues.append(row['name']+': '+row.get('reason','not verified'))
    return issues
