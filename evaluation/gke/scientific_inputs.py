"""Explicit scientific compatibility for separate runs; no verdict imports.

Operational changes are retained separately. Equal inputs do not establish that
runtime capture succeeded or that two populations passed their comparison gates.
"""
from .models import canonical_sha

OPERATIONAL_INFRA = {'controller_image', 'watchdog_image', 'bootstrap_sdk_image', 'controller_boot_image'}
OPERATIONAL_PROTOCOL = {'execution_profile', 'combined_gross_limit_usd'}


def definition(manifest):
    config=manifest.get('config',{})
    files=manifest.get('implementation_hashes',{})
    return dict(product=manifest.get('product_pins'), images=manifest.get('image_lock'),
        thresholds=manifest.get('thresholds'), collection=manifest.get('baseline_config'),
        workloads={k:v for k,v in files.items() if k.startswith('evaluation/workloads/')},
        protocol={k:v for k,v in manifest.get('protocol_definition',{}).items() if k not in OPERATIONAL_PROTOCOL},
        infrastructure={k:v for k,v in config.get('infrastructure',{}).items() if k not in OPERATIONAL_INFRA},
        location={k:config.get('gcp',{}).get(k) for k in ('region','zone')},
        capture={k:config.get(k) for k in ('capture_finalization_version','export_horizon_version','servicegraph_store_max_items')})


def agreement(left,right):
    definitions=[definition(m) for m in (left,right)]
    required={'evaluation/workloads/run.js','evaluation/workloads/workload.json'}
    complete=all(all(d.get(k) for k in ('product','images','thresholds','collection','protocol','infrastructure')) and required<=set(d['workloads']) for d in definitions)
    hashes=[canonical_sha(d) for d in definitions]
    return dict(version='scientific-inputs-v1',state='insufficient' if not complete else 'pass' if hashes[0]==hashes[1] else 'fail',
        sha256=hashes,definitions=definitions,
        differences=[key for key in definitions[0] if definitions[0][key]!=definitions[1][key]],
        operational=[dict(implementation_sha256=m.get('implementation_sha256'),
            images={k:v for k,v in m.get('config',{}).get('infrastructure',{}).items() if k in OPERATIONAL_INFRA},
            protocol={k:v for k,v in m.get('protocol_definition',{}).items() if k in OPERATIONAL_PROTOCOL}) for m in (left,right)],
        limitation='Compare independent cluster identities and apply the same verifier rules separately; equal scientific inputs do not certify validity of observations. Evaluator source changes require a recorded review.')
