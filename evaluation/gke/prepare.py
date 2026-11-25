"""Offline experiment preparation; no cloud mutation."""
from __future__ import annotations
import json,math
from datetime import datetime,timezone
from pathlib import Path
from .models import read,write,sha,canonical_sha,identifier,validate_config,QUALIFICATION,THESIS,REPAIR,REPAIR_V2,COMPLETION,COMPLETION_V2,CAPTURE_V2_SCHEDULES,REPAIR_SCHEDULES,RECOVERABLE_SCHEDULES,build_matrix,implementation_hashes,classify_readiness


def cost_preview(config):
    review=config.get('readiness',{}).get('pricing_review')
    if review is not None:
        if review.get('currency')!='USD' or review.get('credits_assumed')!=0 or not review.get('detail'):
            raise ValueError('gross USD pricing review without credit subtraction required')
        items={}
        for row in review['detail']:
            quantity,rate=float(row['quantity']),float(row['unit_rate_usd'])
            if not all(math.isfinite(v) and v>=0 for v in (quantity,rate)) or row['id'] in items:
                raise ValueError('invalid or duplicate pricing-review term')
            items[row['id']]=quantity*rate
        total=math.fsum(items.values())
        if not math.isfinite(total):raise ValueError('pricing-review total is not finite')
        return dict(review,items=items,gross_estimate=round(total,2),launch_ceiling=config['limits']['launch_ceiling_usd'],gross_limit=config['limits']['gross_limit_usd'],alerts_are_not_enforcement=True)
    # Conservative allowances are assumptions, NOT a live quote or billing cap.
    hours=config['limits']['infrastructure_seconds']/3600
    items={'compute_four_e2_standard_4':4*hours*0.13402284,'gke_management_without_free_credit':hours*.10,'disks_allowance':3.,'network_nat_allowance':5.,'logging_allowance':10.,'registry_and_watchdog_allowance':5.,'gcs_100_GiB_90_days_allowance':10.,'secondary_export_and_transfer_allowance':15.}
    return {'currency':'USD','gross_estimate':round(sum(items.values()),2),'items':items,'credits_assumed':0,'launch_ceiling':config['limits']['launch_ceiling_usd'],'gross_limit':config['limits']['gross_limit_usd'],'kind':'offline estimate with explicit allowances; current SKU verification required before launch','references':['https://cloud.google.com/products/compute/pricing/general-purpose','https://cloud.google.com/kubernetes-engine/pricing','https://cloud.google.com/storage/pricing'],'alerts_are_not_enforcement':True}


def foundation_checkpoint_check(root, config):
    """Catch a stale protected-foundation reference before cloud launch."""
    reference=config.get('gcp',{}).get('foundation_reference')
    if not reference:
        return None
    from types import SimpleNamespace
    from .provision import foundation_source_hashes
    expected=foundation_source_hashes(SimpleNamespace(root=Path(root)))
    valid=reference.get('source_files')==expected
    return {'name':'protected foundation checkpoint','state':'verified' if valid else 'pending',
            'reason':'source matches reviewed foundation reference' if valid else
            'foundation source changed; review the scoped IAM/configuration change, reconcile the existing foundation, and refresh its reference before provisioning',
            'expected_source_files':expected,'referenced_source_files':reference.get('source_files')}


def snapshot_check(path,expected):
    if not path or not Path(path).exists():return {'name':'validated product snapshot','state':'pending','reason':'supply --product-snapshot; no unvalidated checkout is substituted'}
    from ..runner import repository_fingerprint
    if not (Path(path)/'.git').exists() and (Path(path).parent/'product-manifest.json').is_file():
        from .bundle import _product
        record=read(Path(path).parent/'product-manifest.json')
        _product(path,{'karthos_source_hash':expected,'karthos_revision':record.get('original_revision')})
        actual=expected
    else:actual=repository_fingerprint(Path(path))
    return {'name':'validated product snapshot','state':'verified' if actual==expected else 'mismatch','actual':actual,'expected':expected,'reason':'content differs from validated snapshot' if actual!=expected else ''}


def baseline_summary(root,baseline):
    d=read(baseline/'run-report.json');pins=d.get('pins',{})
    if d.get('state')!='complete' or len(d.get('windows',[]))!=16 or not all(w.get('independent_valid') for w in d['windows']):raise ValueError('historical baseline is not a completed valid sixteen-window study')
    summaries=[]
    for row in d['windows']:
        p=baseline/'measured'/row['cell']/'windows'/row['name']/'timing-verification.json'
        with p.open() as source:report=json.load(source)
        parity=report.get('api_storage_parity',{}).get('checks',{})
        resource=report.get('retained_secondary_checks',{}).get('resources',{})
        summaries.append({'cell':row['cell'],'window':row['name'],'independent_valid':report.get('independent_valid'),'api_storage_parity':{k:v.get('state') for k,v in parity.items()},'resource_state':resource.get('state'),'resource_checks':len(resource.get('checks',[])),'fixed_window_fidelity':report.get('fixed_window_fidelity'),'eventual_exact':report.get('eventual_completeness',{}).get('exact'),'duration_parity_populations':[{'labels':x.get('labels'),'timestamp':x.get('timestamp'),'state':x.get('state'),'api_value':x.get('observed'),'storage_value':x.get('reference')} for x in parity.get('duration',{}).get('checks',[])],'duration_scope':'API/storage population only; independent service timing equivalence is not implied','report_sha256':sha(p)})
        del report
    footprint={'files':0,'bytes':0}
    for file in baseline.rglob('*'):
        if file.is_file() and not file.is_symlink():footprint['files']+=1;footprint['bytes']+=file.stat().st_size
    return {'baseline':str(baseline.relative_to(root)),'recorded_evidence_footprint':footprint,'original_unchanged':True,'pins':pins,'windows':summaries,'claims':{'preservation':'supported on evaluated populations','event_window_equality':'not established by API/storage parity','cross_node':'not established by single-node baseline','causal_overhead':'not claimed','scale':'not claimed'}}


def preparation_inputs(root,config,schedule):
    root=Path(root)
    if schedule==COMPLETION_V2:
        from .commit_build import frozen_inputs, INPUT_DIR
        values=frozen_inputs(root)
        directory=root/INPUT_DIR
        summary={'scope':'versioned experiment inputs; not prior-run evidence',
                 'input_path':str(INPUT_DIR),'input_sha256':sha(directory/'experiment-inputs.json')}
        return directory,summary,values['pins'],values['image_lock'],values['thresholds'],values['baseline_config']
    directory=root/config['baseline'];summary=baseline_summary(root,directory)
    return (directory,summary,dict(summary['pins']),read(directory/'runtime-image-lock.json'),
            read(directory/'frozen-thresholds.json'),read(directory/'locked-config.json'))


def prepare(root,config_path,preparation_id,*,product_snapshot=None,schedule=COMPLETION_V2,benchmark=None,product_lock=None,local_evidence=None,continuation_dir=None,continuation_receipt=None,origin_report=None,origin_seal=None,origin_uri=None,matrix_profile=None):
    """Build experiment inputs offline. Human confirmation stays in the conversation."""
    if schedule not in RECOVERABLE_SCHEDULES:
        raise ValueError('new preparations require the active k6-only thesis protocol; legacy schedules are analysis-only')
    root=Path(root).resolve();identifier(preparation_id);config=validate_config(read(config_path),schedule=schedule)
    if schedule in (COMPLETION,COMPLETION_V2) and config.get('execution_segment','all')!='all':
        raise ValueError('completion requires its entire frozen matrix, not an inherited split segment')
    if config.get('execution_profile') and config['execution_profile']!=matrix_profile:raise ValueError('explicit matrix profile must match configured execution profile')
    protocol=read(root/'protocols'/schedule/'protocol.yaml')
    if matrix_profile:
        if schedule!=COMPLETION_V2:raise ValueError('matrix profile requires official completion schedule')
        protocol=dict(protocol,execution_profile=matrix_profile,combined_gross_limit_usd=config['limits']['gross_limit_usd'])
    matrix=build_matrix(protocol,schedule=schedule)
    dest=root/'build/gke/prepared'/preparation_id
    if dest.exists():raise FileExistsError('prepared experiment already exists; choose a new ID')
    baseline,summary,pins,lock,thresholds,baseline_config=preparation_inputs(root,config,schedule)
    snapshot=product_snapshot or config.get('product_snapshot')
    chart_snapshot=None; local=None; thesis_pending=[]
    if schedule in RECOVERABLE_SCHEDULES:
        from .thesis import product_inputs,local_contract
        if not product_lock or not product_snapshot:
            raise ValueError('thesis preparation requires an explicit product snapshot and product lock')
        pins,lock,chart_snapshot,thesis_pending=product_inputs(
            Path(product_snapshot),read(product_lock),pins,lock)
        # This lock already names the exact registry artifacts for this revision.
        config['infrastructure']['product_image_mirrors']={
            name:lock[name+':latest'] for name in ('karthos-backend','karthos-frontend') if name+':latest' in lock}
        if local_evidence:
            local=read(local_evidence)
        try:
            local_contract(local,pins,directory=Path(local_evidence).parent if local_evidence else None)
        except ValueError as exc:
            thesis_pending.append(str(exc))
    origin=None
    if schedule in REPAIR_SCHEDULES:
        from .repair import origin_evidence
        if config.get('execution_segment','all')!='all':raise ValueError('repair requires execution_segment=all')
        if continuation_dir or continuation_receipt:raise ValueError('repair references origins without continuation')
        origin=origin_evidence(origin_report,origin_seal,origin_uri,matrix,
                               expected_protocol=REPAIR if schedule==REPAIR_V2 else THESIS,
                               expected_run_id=protocol.get('origin_run_id'))
    checks=[snapshot_check(snapshot,pins['karthos_source_hash'])]
    foundation_check=foundation_checkpoint_check(root,config)
    if foundation_check is not None:checks.append(foundation_check)
    if not all('@sha256:' in image for image in lock.values()):raise ValueError('baseline contains mutable image identities')
    files=implementation_hashes(root);pending=classify_readiness(config,checks)+thesis_pending;cost=cost_preview(config)
    from .bundle import build_bundle
    bundle=build_bundle(root,preparation_id,snapshot,pins,files,toolchain=config['readiness'].get('toolchain_path'),chart_snapshot=chart_snapshot)
    pending+=bundle['pending']
    checks.append({'name':'execution bundle','state':'verified' if not bundle['pending'] else 'pending','reason':'; '.join(bundle['pending'])})
    replay = None  # No benchmark or synthetic readiness workload in v4.
    minimal_definition={'version':4,'mode':'per-cell-read-only','observation_seconds':120,'traffic':False}
    from .recoverable_run import planning_forecast
    forecast = planning_forecast(config, matrix)
    if forecast.get('missing'):pending.append('reviewed v4 phase timing estimates missing: '+', '.join(forecast['missing']))
    elif not forecast['fits']:pending.append('full v4 time forecast including retries exceeds infrastructure budget')
    if cost['gross_estimate']>config['limits']['launch_ceiling_usd']:pending.append('cost estimate exceeds launch ceiling')
    dest.mkdir(parents=True)
    from .previews import previews
    preview=previews(root,dest/'rendered',baseline,config,lock,preparation_id,chart_snapshot=chart_snapshot,product_snapshot=Path(snapshot) if schedule in RECOVERABLE_SCHEDULES else None,schedule=schedule)
    if preview['state']!='complete':pending.append('rendered manifests contain unresolved images or render errors; inspect rendered/status.json')
    manifest={'schema_version':1,'id':preparation_id,'protocol':protocol['id'],'prepared_at':datetime.now(timezone.utc).isoformat(),
        'study_budget':config.get('readiness',{}).get('study_budget'),'schedule':schedule,'readiness_check':None,'minimal_readiness':minimal_definition,'time_forecast':forecast,'readiness_benchmark':replay,'config':config,'protocol_definition':protocol,'matrix':matrix,'implementation_hashes':files,'implementation_sha256':canonical_sha(files),
        'product_pins':pins,'product_snapshot':str(Path(snapshot).resolve()) if snapshot else None,'image_lock':lock,
        'thresholds':thresholds,'baseline_config':baseline_config,
        'artifact_checks':checks,'pending_prerequisites':pending,'execution_ready':not pending,'cost_preview':cost,'bundle':bundle,'rendered_previews':preview}
    if schedule in CAPTURE_V2_SCHEDULES:
        from .models import validate_v2_inputs
        for key in ('capture_finalization_version','servicegraph_store_max_items'):
            manifest[key]=config.get(key)
        try:validate_v2_inputs(manifest,preparation_id)
        except ValueError as exc:pending.append(str(exc))
        from .repair import validate_budget
        try:validate_budget(manifest)
        except ValueError as exc:pending.append(str(exc))
        quarantine=config.get('quarantine',{})
        manifest['quarantine_requirements']={'bucket':quarantine.get('bucket'),'prefix':quarantine.get('prefix'),
            'required_permissions':['storage.objects.create','storage.objects.get'],
            'remote_permissions_verified':False,'scope':'current-run prefix conditional IAM only; operator review before cloud changes'}
        manifest['execution_ready']=not pending
    if origin is not None:manifest['origin_evidence']=origin
    if continuation_dir or continuation_receipt:
        if not continuation_dir or not continuation_receipt:raise ValueError('continuation needs checkpoint directory and export receipt')
        from .recoverable_run import load_continuation
        manifest['continuation']=load_continuation(continuation_dir,continuation_receipt,manifest)
        manifest['time_forecast']=planning_forecast(config,matrix,manifest['continuation'])
        pending[:]=[item for item in pending if not item.startswith(('reviewed v4 phase timing estimates missing:', 'full v4 time forecast including retries exceeds'))]
        final_forecast=manifest['time_forecast']
        if final_forecast.get('missing'):pending.append('reviewed v4 phase timing estimates missing: '+', '.join(final_forecast['missing']))
        elif not final_forecast['fits']:pending.append('full v4 time forecast including retries exceeds infrastructure budget')
        manifest['execution_ready']=not pending
    if schedule in RECOVERABLE_SCHEDULES:
        manifest['local_contract_evidence']=local
        if schedule==COMPLETION_V2:
            manifest['experiment_inputs']=summary
        else:
            manifest['historical_timing_inputs']={'baseline':summary['baseline'],'product_pins':summary['pins']}
    write(dest/'readiness-matrix.json',[]);write(dest/'manifest.json',manifest);write(dest/'matrix.json',matrix);write(dest/'prior-evidence-analysis.json',summary)
    pending_lines='\n'.join('- '+issue for issue in pending) or '- None.'
    write(dest/'README.md',f"# Prepared GKE experiment: {preparation_id}\n\n{len(matrix)} windows; {sum(row['duration_seconds'] for row in matrix)//60} scheduled matrix load minutes. Per-cell readiness is read-only; recoverable execution failures have at most one labeled retry. The {config['limits']['infrastructure_seconds']//3600}-hour infrastructure allowance includes cleanup.\n\nRead the architecture and phase plan in {('docs/thesis-completion-v2.md' if schedule==COMPLETION_V2 else 'docs/thesis-completion-v1.md' if schedule==COMPLETION else 'docs/thesis-repair-validation-v2.md' if schedule==REPAIR_V2 else 'docs/thesis-qualification-v4.md')}. No review commands, approval receipts or signatures are needed. The operator reviews this setup and gives explicit approval before any cloud work.\n\n## Pending technical inputs\n\n{pending_lines}\n\nmanifest.json records the frozen configuration and source/artifact hashes for reproducibility. Checksums on collected evidence and ownership/deadline safeguards are retained. No cloud work was executed by preparation.\n")
    return dest/'manifest.json'
