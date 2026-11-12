import json
import tempfile
import unittest
from pathlib import Path

from evaluation.verify import (
    counter_increase,
    k6_requests,
    paginate_documents,
    compare_provenance,
    compare_entity_sets,
)


class VerifyTests(unittest.TestCase):
    def test_counter_reset_preserves_increase(self):
        self.assertEqual(counter_increase([100, 107, 2, 6]), 13)

    def test_k6_uses_actual_completed_requests_and_time_window(self):
        records = [
            {"type": "Point", "metric": "http_reqs", "data": {"time": "2026-10-18T12:00:01Z", "value": 1, "tags": {"route": "home", "status": "200"}}},
            {"type": "Point", "metric": "http_reqs", "data": {"time": "2026-10-18T12:00:02Z", "value": 1, "tags": {"route": "cart", "status": "500"}}},
            {"type": "Point", "metric": "iterations", "data": {"time": "2026-10-18T12:00:02Z", "value": 1, "tags": {}}},
            {"type": "Point", "metric": "http_reqs", "data": {"time": "2026-10-18T12:02:02Z", "value": 1, "tags": {"route": "home", "status": "200"}}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "k6.ndjson"
            path.write_text("\n".join(json.dumps(record) for record in records))
            result = k6_requests(path, "2026-10-18T12:00:00Z", "2026-10-18T12:01:00Z")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["by_route"], {"home": 1, "cart": 1})
        self.assertEqual(result["errors"], 1)

    def test_pagination_collects_all_pages_and_rejects_repeated_token(self):
        docs = [
            {"data": {"edges": [{"id": "one"}], "total": 2}, "next_token": "page2"},
            {"data": {"edges": [{"id": "two"}], "total": 2}},
        ]
        self.assertEqual([x["id"] for x in paginate_documents(docs, "edges")], ["one", "two"])
        with self.assertRaises(ValueError):
            paginate_documents([docs[0], docs[0]], "edges")

    def test_provenance_wrong_source_is_failure(self):
        samples = [{"labels": {"source": "mesh.istio"}}]
        result = compare_provenance(samples, "traces.servicegraph")
        self.assertEqual(result["wrong"], 1)
        self.assertEqual(result["missing"], 0)

    def test_entity_comparison_reports_missing_and_extra(self):
        expected = {("service", "boutique", "frontend"), ("service", "boutique", "cart")}
        actual = {("service", "boutique", "frontend"), ("service", "boutique", "ad")}
        result = compare_entity_sets(expected, actual)
        self.assertEqual(result["missing"], [("service", "boutique", "cart")])
        self.assertEqual(result["extra"], [("service", "boutique", "ad")])


if __name__ == "__main__":
    unittest.main()

class LoadQualityTests(unittest.TestCase):
    def test_dropped_iterations_detected(self):
        import tempfile
        from pathlib import Path
        from evaluation.verify import k6_quality
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'k6.ndjson'
            path.write_text('{"type":"Point","metric":"dropped_iterations","data":{"time":"2026-10-18T12:00:00Z","value":2,"tags":{}}}\n')
            self.assertEqual(k6_quality(path)['dropped_iterations'],2)
