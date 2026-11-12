"""Offline paired benchmark of the window evidence pipeline."""
from __future__ import annotations

import argparse
import importlib.util
import hashlib
import math
import platform
import resource
import shutil
import statistics
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from . import export
from .models import sha, write

ARCHIVE_SHA256 = {
    'smoke':'73568138275bb9348c45ff213a7d6a13481bf7b38227b14a05c182403aa4680c',
    'astronomy':'6b2da93d7d3cfd95b6e52b9d32a64dbb296b51bd5c8ea30d127c34b43c507ed3',
    'boutique':'fd566813a8ddf9307d07ecd12baccdb8c76663986948e4a8bc3cd48b701166c8',
    'network':'7957cf15b0d88dbdf07cffaab42875209f4d15d15e26816ba6f0b1e85657f832',
}
BASELINE_EXPORT_SHA256 = '5bb8ee3e8956575dac6d8fb1720f9fa3ad345d18defb49fa61ebe39d6ad21774'


def privacy_fingerprint():
    return sha(Path(export.__file__))


def per_input_reductions(cases):
    return {name: 1-statistics.median(
                trial['candidate']['seconds'] / trial['baseline']['seconds']
                for trial in cases[name]['trials'])
            for name in ('astronomy', 'boutique')}


def validated_receipt(report):
    if not isinstance(report, dict) or report.get('privacy_fingerprint') != privacy_fingerprint():
        raise ValueError('offline benchmark missing or for a different evidence implementation')
    try:
        cases = report['cases']
        paired = [sum(cases[k]['trials'][i]['candidate']['seconds'] for k in ('astronomy','boutique'))
                  / sum(cases[k]['trials'][i]['baseline']['seconds'] for k in ('astronomy','boutique'))
                  for i in range(3)]
        if ({key:item['input_sha256'] for key,item in cases.items()} != ARCHIVE_SHA256
                or report['baseline_sha256'] != BASELINE_EXPORT_SHA256
                or any(len(item['trials']) != 3 for item in cases.values())
                or any(not math.isfinite(ratio) or ratio <= 0 for ratio in paired)
                or not math.isclose(report['median_reduction'],1-statistics.median(paired))
                or not report['environment']['platform'].startswith('Linux')
                or report.get('per_input_median_reduction') != per_input_reductions(cases)
                or report['passed'] is not True or 1-statistics.median(paired) < .30
                or any(not math.isfinite(value) or value < .30 for value in per_input_reductions(cases).values())):
            raise ValueError('offline replay does not meet the 30% timing target')
    except (AttributeError, KeyError, IndexError, TypeError, ZeroDivisionError) as exc:
        raise ValueError('invalid offline benchmark report') from exc
    return report


def load_baseline(path):
    spec = importlib.util.spec_from_file_location('evaluation.gke._benchmark_baseline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unpack(archive, expected, directory):
    if sha(archive) != expected:
        raise ValueError('historical outer archive checksum mismatch')
    seen = set()
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or '..' in name.parts or name in seen:
                raise ValueError('unexpected historical archive member: '+str(name))
            seen.add(name)
            source.extract(member, directory, filter='data')
    if not export.verify(directory)['valid']:
        raise ValueError('historical inner seal failed')


def run_pipeline(module, original, work):
    def measured(label, action):
        start = time.monotonic()
        cpu = time.process_time()
        result = action()
        timings[label] = dict(seconds=time.monotonic()-start, cpu_seconds=time.process_time()-cpu)
        return result

    timings = {}
    projection = work/'projection'
    archives = work/'archives'
    archives.mkdir()
    result = measured('projection', lambda: module.export_projection(original, projection))
    if not result['valid']:
        raise ValueError('projection seal failed')
    shutil.copy2(original/'GKE-SHA256SUMS', projection/'LOCAL-ORIGINAL-GKE-SHA256SUMS')
    if not module.seal(projection)['valid']:
        raise ValueError('archive input seal failed')
    parts = measured('packing', lambda: module.pack_window_archives(projection, archives))
    publishable = work/'publishable'
    result = measured('privacy_verification', lambda: module.export_projection(archives, publishable))
    if not result['valid']:
        raise ValueError('publication seal failed')
    return dict(timings=timings, seconds=sum(x['seconds'] for x in timings.values()),
                peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                archive_bytes=sum(x['bytes'] for x in parts),
                evidence_seal=(projection/'GKE-SHA256SUMS').read_text(),
                members=result['checked'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--baseline-export', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--trials', type=int, default=3)
    args = parser.parse_args()
    if args.trials < 1 or args.report.exists():
        parser.error('positive trial count and unused report path required')
    if platform.system() != 'Linux':
        parser.error('run both implementations in the same Linux environment')
    if sha(args.baseline_export) != BASELINE_EXPORT_SHA256:
        parser.error('pre-change export implementation hash differs')
    baseline = load_baseline(args.baseline_export)
    fingerprint = privacy_fingerprint()
    cases = {}
    for label in ('smoke', 'astronomy', 'boutique', 'network'):
        with tempfile.TemporaryDirectory(prefix='gke-benchmark-') as folder:
            root = Path(folder)
            original = root/'original'
            original.mkdir()
            expected = (args.fixtures/(label+'.sha256')).read_text().strip()
            if expected != ARCHIVE_SHA256[label]:
                raise ValueError('historical fixture pin differs for '+label)
            unpack(args.fixtures/(label+'.tar.gz'), expected, original)
            trials = []
            for trial in range(args.trials):
                pair = {}
                implementations = [('baseline', baseline), ('candidate', export)]
                if trial % 2:
                    implementations.reverse()
                for name, module in implementations:
                    with tempfile.TemporaryDirectory(dir=root) as work:
                        pair[name] = run_pipeline(module, original, Path(work))
                if pair['baseline'].pop('evidence_seal') != pair['candidate'].pop('evidence_seal'):
                    raise ValueError('projected evidence differs for '+label)
                trials.append(pair)
                print(label, trial+1, {key: round(value['seconds'], 2) for key,value in pair.items()}, flush=True)
            cases[label] = {'input_sha256': expected, 'trials': trials}
            write(args.report.with_suffix('.partial.json'), {'cases': cases})
    paired = [sum(cases[k]['trials'][i]['candidate']['seconds'] for k in ('astronomy','boutique'))
              / sum(cases[k]['trials'][i]['baseline']['seconds'] for k in ('astronomy','boutique'))
              for i in range(args.trials)]
    improvement = 1 - statistics.median(paired)
    reductions = per_input_reductions(cases)
    write(args.report, {'cases': cases, 'median_reduction': improvement,
                        'per_input_median_reduction': reductions,
                        'privacy_fingerprint': fingerprint, 'baseline_sha256': sha(args.baseline_export),
                        'environment': {'platform': platform.platform(), 'python': platform.python_version()},
                        'passed': args.trials == 3 and improvement >= .30 and all(x >= .30 for x in reductions.values()),
                        'limitations': 'Previously projected evidence; credential regressions are exercised separately.'})


if __name__ == '__main__':
    main()
