"""Additive reporting for evaluate._numeric resource checks; never rescores them."""
import math


def details(check):
    observed=check.get('observed') or {}
    if not isinstance(observed,dict):observed={}
    value=observed.get('value');reference=check.get('reference_value')
    numeric=check.get('state') in ('pass','fail') and all(type(v) in (int,float) and math.isfinite(v) for v in (value,reference))
    error=abs(value-reference) if numeric else None
    return dict(state=check.get('state','insufficient'),reason=check.get('reason'),
        metric=observed.get('metric',check.get('metric')),unit=observed.get('unit'),
        identity={k:observed.get(k) for k in ('node_uid','pod_uid','container_id','source','dimensions','measurement_timestamp','recorded_timestamp')},
        reference=reference,observed=value,absolute_error=error,
        relative_error=error/abs(reference) if numeric and reference!=0 else None,
        tolerance=dict(relative=1e-12,absolute=1e-9,bound=max(1e-9,1e-12*max(abs(value),abs(reference))),
            rule='math.isclose symmetric relative bound; existing exact resource conversion comparator') if numeric else None)
