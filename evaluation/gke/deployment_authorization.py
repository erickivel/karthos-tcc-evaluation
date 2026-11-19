"""Bounded, evidenced controller authorization before any application setup."""
from __future__ import annotations

import copy
import json
import re
import time
import uuid

from .recoverable_run import SafetyFailure

BINDING = 'karthos-evaluation-controller-bootstrap'
OWNER = 'gke-qualification-v1'
CLUSTER_ANNOTATION = 'evaluation.karthos.dev/cluster-uid'
BOOTSTRAP_WAIT_SECONDS = 600
RBAC = '/apis/rbac.authorization.k8s.io/v1'


class AuthorizationFailure(SafetyFailure):
    """Configuration failure: no unchanged operational retry is permitted."""


def _labels(ctx):
    return {'evaluation.karthos.dev/owner': OWNER, 'evaluation.karthos.dev/run-id': ctx.run_id}


def validate_binding(value, ctx, cluster_uid, email):
    meta = value.get('metadata', {})
    expected = {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': 'cluster-admin'}
    subject = {'kind': 'User', 'apiGroup': 'rbac.authorization.k8s.io', 'name': email}
    if (meta.get('name') != BINDING or not meta.get('uid')
            or any(meta.get('labels', {}).get(k) != v for k, v in _labels(ctx).items())
            or meta.get('annotations', {}).get(CLUSTER_ANNOTATION) != cluster_uid
            or value.get('roleRef') != expected or value.get('subjects') != [subject]):
        raise AuthorizationFailure('controller bootstrap binding identity/ownership/role mismatch')
    return meta['uid']


def verify(ctx, repeat, *, clock=time.time, sleep=time.sleep):
    if ctx.config.get('execution_profile') != 'thesis-robustness-full-v1':
        return {'state': 'not-required', 'verified': False}
    receipt = {'repeat': repeat, 'run_id': ctx.run_id, 'verified': False,
               'probe_cleanup_verified': False, 'permissions': [], 'probe_objects': []}
    path = ctx.result/'infrastructure'/f'authorization-repeat-{repeat}.json'
    expires = min(clock()+BOOTSTRAP_WAIT_SECONDS, ctx.deadline-ctx.config['limits']['cleanup_reserve_seconds'])

    def kube(*args, cleanup=False, **kwargs):
        remaining = (ctx.deadline if cleanup else expires)-clock()
        if remaining <= 0:
            raise AuthorizationFailure('controller authorization bootstrap/probe deadline reached')
        return ctx.kube(*args, timeout=min(30, remaining), cleanup=cleanup, **kwargs)

    def document(*args, **kwargs):
        result = kube(*args, **kwargs)
        if getattr(result, 'returncode', 0):
            raise AuthorizationFailure('controller authorization API request rejected')
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise AuthorizationFailure('controller authorization API returned malformed object')
        return value

    try:
        from .provision import assert_project_lease, verify_resource
        assert_project_lease(ctx)
        resources = [r for r in ctx.state.get('resources', []) if r.get('kind') == 'cluster']
        if len(resources) != 1 or not resources[0].get('uid'):
            raise AuthorizationFailure('exactly one UID-bound cluster required for authorization')
        cluster = resources[0]
        if verify_resource(ctx, cluster) is None:
            raise AuthorizationFailure('authorization cluster no longer exists')
        cluster_uid = str(cluster['uid'])
        email = ctx.state.get('terraform', {}).get('foundation', {}).get('controller_service_account', '')
        if not isinstance(email, str) or not re.fullmatch(r'[a-zA-Z0-9._-]+@[a-zA-Z0-9.-]+\.iam\.gserviceaccount\.com', email):
            raise AuthorizationFailure('expected controller identity unavailable')
        receipt.update(cluster_uid=cluster_uid, cluster_name=cluster['name'], expected_principal=email,
                       bootstrap_deadline=expires, started_at=clock())
        ctx.event('authorization-required', repeat=repeat, cluster_uid=cluster_uid,
                  binding_name=BINDING, expected_principal=email, authorization_deadline=expires)
        while True:
            response = kube('get', 'clusterrolebinding', BINDING, '--ignore-not-found', '-o', 'json')
            if getattr(response, 'returncode', 0):
                raise AuthorizationFailure('controller bootstrap binding read denied')
            if response.stdout.strip():
                value = json.loads(response.stdout)
                if not isinstance(value, dict):
                    raise AuthorizationFailure('controller bootstrap binding malformed')
                receipt['binding_uid'] = validate_binding(value, ctx, cluster_uid, email)
                break
            remaining = expires-clock()
            if remaining <= 0:
                raise AuthorizationFailure('controller authorization bootstrap deadline reached')
            sleep(min(5, remaining))
        # Re-observe the resource and lease after the externally completed handshake.
        assert_project_lease(ctx)
        if verify_resource(ctx, cluster) is None:
            raise AuthorizationFailure('authorization cluster disappeared during bootstrap')
        identity = document('create', '--raw', '/apis/authentication.k8s.io/v1/selfsubjectreviews', '-f', '-',
                            input_text=json.dumps({'apiVersion': 'authentication.k8s.io/v1', 'kind': 'SelfSubjectReview'}))
        username = identity.get('status', {}).get('userInfo', {}).get('username')
        receipt['observed_principal'] = username
        if username != email:
            raise AuthorizationFailure('controller authenticated identity differs from expected email')
        # The harness installs cluster-wide collectors, CRDs and namespaced workloads.
        # Wildcard access plus explicit RBAC checks avoid assuming a named binding is effective.
        checks = [{'group': '*', 'resource': '*', 'verb': '*'}]
        checks += [{'group': 'rbac.authorization.k8s.io', 'resource': resource, 'verb': verb}
                   for resource in ('clusterroles', 'clusterrolebindings', 'roles', 'rolebindings')
                   for verb in ('create', 'patch', 'update', 'delete')]
        for attributes in checks:
            review = document('create', '--raw', '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews', '-f', '-',
                              input_text=json.dumps({'apiVersion': 'authorization.k8s.io/v1', 'kind': 'SelfSubjectAccessReview',
                                                     'spec': {'resourceAttributes': attributes}}))
            status = review.get('status', {})
            allowed = (isinstance(status, dict) and status.get('allowed') is True
                       and ('denied' not in status or status['denied'] is False)
                       and ('evaluationError' not in status or status['evaluationError'] == ''
                            and isinstance(status['evaluationError'], str)))
            receipt['permissions'].append({'attributes': attributes, 'allowed': allowed})
            if not allowed:
                raise AuthorizationFailure('required controller deployment permission denied or malformed')
        _probe(ctx, receipt, cluster_uid, email, kube, document)
        receipt.update(verified=True, finished_at=clock())
        ctx.event('authorization-verified', repeat=repeat, cluster_uid=cluster_uid, principal=email)
        return receipt
    except Exception as exc:
        receipt['error'] = {'type': type(exc).__name__, 'message': str(exc)[:1500]}
        if isinstance(exc, AuthorizationFailure):
            raise
        raise AuthorizationFailure('controller authorization failed: '+str(exc)[:1000]) from exc
    finally:
        ctx.write(path, receipt)


def _probe(ctx, receipt, cluster_uid, email, kube, document):
    """Exercise the verbs Helm needs, without a Pod or any benchmark traffic."""
    name = 'karthos-auth-probe-'+uuid.uuid4().hex[:20]
    metadata = {'name': name, 'labels': _labels(ctx), 'annotations': {CLUSTER_ANNOTATION: cluster_uid}}
    objects = []
    for kind, resource, namespaced in [('ClusterRole', 'clusterroles', False), ('Role', 'roles', True),
                                      ('ClusterRoleBinding', 'clusterrolebindings', False), ('RoleBinding', 'rolebindings', True)]:
        obj = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': kind, 'metadata': copy.deepcopy(metadata)}
        if namespaced:
            obj['metadata']['namespace'] = 'default'
        if kind.endswith('Binding'):
            obj.update(roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role' if namespaced else 'ClusterRole', 'name': name},
                       subjects=[{'kind': 'User', 'apiGroup': 'rbac.authorization.k8s.io', 'name': email}])
        else:
            obj['rules'] = [{'apiGroups': [''], 'resources': ['configmaps' if namespaced else 'namespaces'], 'verbs': ['get']}]
        collection = RBAC+('/namespaces/default' if namespaced else '')+'/'+resource
        objects.append({'object': obj, 'resource': resource, 'collection': collection,
                        'namespace_args': ('-n', 'default') if namespaced else (), 'created': None})
    attempted = []
    cleanup_errors = []
    try:
        for item in objects:
            attempted.append(item)
            obj = document('create', '--raw', item['collection'], '-f', '-', input_text=json.dumps(item['object']))
            _check_probe(obj, item, cluster_uid, ctx)
            item['created'] = copy.deepcopy(obj)
            receipt['probe_objects'].append({'kind': obj['kind'], 'name': name, 'uid': obj['metadata']['uid']})
            patch = [{'op': 'test', 'path': '/metadata/uid', 'value': obj['metadata']['uid']},
                     {'op': 'test', 'path': '/metadata/resourceVersion', 'value': obj['metadata']['resourceVersion']},
                     {'op': 'add', 'path': '/metadata/annotations/evaluation.karthos.dev~1probe-patched', 'value': 'true'}]
            obj = document('patch', item['resource'], name, *item['namespace_args'], '--type=json', '-p', json.dumps(patch), '-o', 'json')
            _check_probe(obj, item, cluster_uid, ctx)
            obj['metadata'].setdefault('annotations', {})['evaluation.karthos.dev/probe-updated'] = 'true'
            updated = document('replace', '-f', '-', '-o', 'json', input_text=json.dumps(obj))
            _check_probe(updated, item, cluster_uid, ctx)
    finally:
        for item in reversed(attempted):
            try:
                response = kube('get', item['resource'], name, *item['namespace_args'], '--ignore-not-found', '-o', 'json', cleanup=True)
                if getattr(response, 'returncode', 0):
                    raise AuthorizationFailure('probe cleanup observation rejected')
                if not response.stdout.strip():
                    continue
                actual = json.loads(response.stdout)
                _check_probe(actual, item, cluster_uid, ctx)
                options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
                    'uid': actual['metadata']['uid'], 'resourceVersion': actual['metadata']['resourceVersion']}}
                document('delete', '--raw', item['collection']+'/'+name, '-f', '-', input_text=json.dumps(options), cleanup=True)
                kube('wait', '--for=delete', item['resource']+'/'+name, *item['namespace_args'], '--timeout=30s', cleanup=True)
                after = kube('get', item['resource'], name, *item['namespace_args'], '--ignore-not-found', '-o', 'json', cleanup=True)
                if getattr(after, 'returncode', 0) or after.stdout.strip():
                    raise AuthorizationFailure('probe still present after cleanup')
            except Exception as exc:
                cleanup_errors.append({'kind': item['object']['kind'], 'error': str(exc)[:500]})
        receipt['probe_cleanup_verified'] = not cleanup_errors
        receipt['probe_cleanup_errors'] = cleanup_errors
        if cleanup_errors:
            raise AuthorizationFailure('controller authorization probe cleanup unverified')


def _check_probe(obj, item, cluster_uid, ctx):
    meta = obj.get('metadata', {})
    expected = item['object']
    if (obj.get('kind') != expected['kind'] or meta.get('name') != expected['metadata']['name']
            or meta.get('namespace') != expected['metadata'].get('namespace')
            or not meta.get('uid') or not meta.get('resourceVersion')
            or any(meta.get('labels', {}).get(k) != v for k, v in _labels(ctx).items())
            or meta.get('annotations', {}).get(CLUSTER_ANNOTATION) != cluster_uid
            or any(obj.get(key) != expected.get(key) for key in ('rules', 'roleRef', 'subjects'))
            or (item['created'] and meta['uid'] != item['created']['metadata']['uid'])):
        raise AuthorizationFailure('authorization probe identity/ownership changed')
