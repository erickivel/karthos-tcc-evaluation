import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from evaluation.workloads import locust_reports
except ImportError:
    locust_reports = None

class Signal:
    def __init__(self): self.listeners=[]
    def add_listener(self, listener): self.listeners.append(listener)
    def fire(self, **kwargs):
        for listener in self.listeners: listener(**kwargs)

class FinalReportTests(unittest.TestCase):
    def test_final_snapshot_uses_native_api_after_shutdown_and_replaces_periodic_files(self):
        self.assertIsNotNone(locust_reports, 'final Locust CSV export is not implemented')
        events=SimpleNamespace(init=Signal(),quit=Signal())
        calls=[]
        class Writer:
            def __init__(self,environment,percentiles):calls.append(('init',environment,percentiles))
            def requests_csv(self,writer):writer.writerows([['Name','Request Count'],['Aggregated',160]])
            def failures_csv(self,writer):writer.writerows([['Error','Occurrences'],['expected',80]])
            def exceptions_csv(self,writer):writer.writerow(['Count','Message'])
        with tempfile.TemporaryDirectory() as tmp:
            prefix=Path(tmp)/'locust'
            old=Path(str(prefix)+'_stats.csv');old.write_text('periodic snapshot: 116\n')
            env=SimpleNamespace(parsed_options=SimpleNamespace(csv_prefix=str(prefix)))
            locust_reports.install(events,Writer,[.5,.95])
            events.init.fire(environment=env)
            self.assertIn('116',old.read_text())
            events.quit.fire(exit_code=1)
            self.assertEqual(list(csv.DictReader(old.read_text().splitlines()))[0]['Request Count'],'160')
            self.assertTrue(Path(str(prefix)+'_failures.csv').is_file())
            self.assertTrue(Path(str(prefix)+'_exceptions.csv').is_file())
            self.assertEqual(calls,[('init',env,[.5,.95])])

    def test_missing_csv_option_does_not_create_reports(self):
        self.assertIsNotNone(locust_reports)
        events=SimpleNamespace(init=Signal(),quit=Signal())
        with patch.object(locust_reports,'write_final') as write:
            locust_reports.install(events,object,[])
            events.init.fire(environment=SimpleNamespace(parsed_options=SimpleNamespace(csv_prefix=None)))
            events.quit.fire(exit_code=0)
            write.assert_not_called()

if __name__=='__main__':unittest.main()
