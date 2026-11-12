"""Kubelet Summary source values, with lifecycle and acquisition provenance."""
from types import FunctionType, SimpleNamespace
import threading


def acquire_summary(node, runner, *, cadence_seconds=1.0, stop_event=None):
    """Reuse existing bounded read, injecting kube command without global mutation.

    runner has evaluation.runner.kube's signature; invocation is explicit only.
    The first attempt retains the legacy 5 second timeout, then the existing
    cadence budget applies to retries. This adapter does not claim a strict
    one-second hard timeout.
    """
    from evaluation.structured_capture import FastCapture
    method = FastCapture._read_kubelet_json
    context = dict(method.__globals__, r=SimpleNamespace(kube=runner))
    bounded_read = FunctionType(method.__code__, context, argdefs=method.__defaults__)
    state = SimpleNamespace(node=node, PERIODS={'kubelet': cadence_seconds},
                            stop_event=stop_event or threading.Event(),
                            acquisition_recoveries={'kubelet': 0})
    row = {}
    requested = bounded_read(state, row)
    row['acquisition_started_at'] = requested / 1e9
    row['acquisition_ended_at'] = row['acquisition_received_at_ns'] / 1e9
    return row


_UNITS = {'usageNanoCores': 'nanocores', 'usageCoreNanoSeconds': 'core_nanoseconds',
          'usageBytes': 'bytes', 'workingSetBytes': 'bytes', 'availableBytes': 'bytes',
          'capacityBytes': 'bytes', 'usedBytes': 'bytes', 'rssBytes': 'bytes',
          'pageFaults': 'faults', 'majorPageFaults': 'faults',
          'inodes': 'inodes', 'inodesFree': 'inodes', 'inodesUsed': 'inodes'}


def read_summary(summary, *, node, requested_at, received_at):
    """Flatten raw values without OTel conversions; absent values stay absent."""
    if (not isinstance(summary, dict) or summary.get('node', {}).get('nodeName') != node
            or not isinstance(summary.get('pods'), list)):
        raise ValueError('invalid summary or source node mismatch')
    if received_at < requested_at:
        raise ValueError('inverted acquisition interval')
    rows = []

    def emit(obj, identity, dimensions=None):
        dimensions = dimensions or {}
        for group in ('cpu', 'memory', 'fs', 'rootfs', 'logs', 'ephemeral-storage'):
            stats = obj.get(group) or {}
            for field, unit in _UNITS.items():
                if stats.get(field) is not None:
                    rows.append(dict(identity, metric=group+'.'+field, value=stats[field],
                                     unit=unit, dimensions=dict(dimensions),
                                     measurement_timestamp=stats.get('time')))
        net = obj.get('network') or {}
        interfaces = net.get('interfaces')
        if interfaces is None:
            interfaces = [net] if net.get('name') else []
        for interface in interfaces:
            for field, direction in (('rxBytes', 'receive'), ('txBytes', 'transmit')):
                if interface.get(field) is not None:
                    rows.append(dict(identity, metric='network.bytes', value=interface[field], unit='bytes',
                                     dimensions=dict(dimensions, interface=interface.get('name'), direction=direction),
                                     measurement_timestamp=net.get('time')))

    base = dict(node=node, source='kubelet-summary', acquisition_started_at=requested_at,
                acquisition_ended_at=received_at)
    emit(summary['node'], dict(base, scope='node', node_start_time=summary['node'].get('startTime')))
    for pod in summary['pods']:
        ref = pod.get('podRef') or {}
        identity = dict(base, scope='pod', namespace=ref.get('namespace'), pod=ref.get('name'),
                        pod_uid=ref.get('uid'), pod_start_time=pod.get('startTime'))
        emit(pod, identity)
        for container in pod.get('containers') or []:
            emit(container, dict(identity, scope='container', container=container.get('name'),
                                 container_start_time=container.get('startTime')))
        for volume in pod.get('volume') or []:
            emit({'fs': volume}, dict(identity, scope='volume'), {'volume': volume.get('name'),
                 'pvc_name': (volume.get('pvcRef') or {}).get('name'),
                 'pvc_namespace': (volume.get('pvcRef') or {}).get('namespace')})
    return rows
