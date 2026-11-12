"""Final CSV snapshots through Locust's own reporting API.

Pinned Locust 2.43/2.44 periodically rewrites CSVs; the last periodic snapshot
may precede test completion. The quit event follows runner.quit(), including
in-flight stop timeout. Atomic replacement prevents an old periodic writer's
open file handles from overwriting these final snapshots.
"""
from __future__ import annotations
import csv
import os
from pathlib import Path


def write_final(environment, prefix, writer_type, percentiles):
    reporter = writer_type(environment, percentiles)
    for suffix, method in (('stats',reporter.requests_csv),
                           ('failures',reporter.failures_csv),
                           ('exceptions',reporter.exceptions_csv)):
        destination = Path(str(prefix)+'_'+suffix+'.csv')
        temporary = destination.with_name(destination.name+'.final-tmp')
        try:
            with temporary.open('w',newline='',encoding='utf-8') as output:
                method(csv.writer(output))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary,destination)
        finally:
            temporary.unlink(missing_ok=True)


def install(events, writer_type=None, percentiles=None):
    if writer_type is None:
        from locust.stats import StatsCSV, PERCENTILES_TO_REPORT
        writer_type, percentiles = StatsCSV, PERCENTILES_TO_REPORT
    def initialized(environment=None, **kwargs):
        nonlocal environment_ref
        environment_ref=environment
    environment_ref=None
    def finished(**kwargs):
        if environment_ref is None:return
        prefix=getattr(environment_ref.parsed_options,'csv_prefix',None)
        if prefix:
            write_final(environment_ref,prefix,writer_type,percentiles)
    events.init.add_listener(initialized)
    events.quit.add_listener(finished)
