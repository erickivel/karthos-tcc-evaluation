import copy,json,tempfile,unittest
from pathlib import Path
from evaluation.diagnostics import astronomy_packet_probe as probe
from evaluation.generator_artifacts import validate_export
from evaluation.tests.test_generator_artifacts import bundle,rehash
from evaluation.protocol import Cell

class IdleBoundaryTests(unittest.TestCase):
    def test_isolated_script_job_uses_closed_mode_and_direct_target(self):
        self.assertTrue(hasattr(probe,"idle_boundary_job"),"idle builder is missing")
        job=probe.idle_boundary_job(Cell("astronomy","mesh",1),"r","http://10.42.0.10:8080")
        self.assertEqual(job["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"],"eval-k6-idle-boundary")
        env={x["name"]:x["value"] for x in job["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertNotIn("RATE",env)
        self.assertEqual(env["TARGET"],"http://10.42.0.10:8080")
        self.assertEqual(env["WORKLOAD"],"idle-boundary")
        cm=probe.idle_script_configmap()
        self.assertEqual(cm["metadata"]["name"],"eval-k6-idle-boundary")
        source=cm["data"]["run.js"]
        for token in ["per-vu-iterations","iterations: 21","vus: 1","maxDuration: '300s'","5.995","5.998","5.999","6.000","6.001","6.002","6.005","Connection","traceparent","EVAL_ATTEMPT","EVAL_REQUEST","EVAL_WINDOW"]:self.assertIn(token,source)
        self.assertNotIn("constant-arrival-rate",source)

    def fixture(self,path):
        bundle(path)
        first=dict(run_id="run",mode="idle-boundary",offered_model="closed-per-vu-iterations",started_at="2026-10-20T00:00:00Z",ended_at=None,expected_pairs=21,expected_requests=42,expected_iterations=21)
        records=[dict(run_id="run",request_id=f"run:{n}:{phase}",pair_index=n,phase=phase,started_at="2026-10-20T00:00:00Z",completed_at="2026-10-20T00:00:01Z") for n in range(21) for phase in ("warm","probe")]
        events=[("WINDOW",first)]+[event for row in records for event in [("ATTEMPT",dict(row)),("REQUEST",row)]]+[("WINDOW",dict(first,ended_at="2026-10-20T00:02:10Z"))]
        (path/"generator.log").write_text("".join("EVAL_"+kind+" "+json.dumps(row)+"\n" for kind,row in events))
        (path/"k6-summary.json").write_text(json.dumps(dict(metrics=dict(http_reqs=dict(values=dict(count=42)),iterations=dict(values=dict(count=21))))))
        (path/"k6.ndjson").write_text(json.dumps(dict(type="Point",metric="http_reqs",data=dict(value=42)))+"\n")
        rehash(path)

    def test_closed_mode_missing_dropped_metric_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);self.fixture(p)
            result=validate_export(p,"k6","idle-boundary","run")
            self.assertEqual(result["requests"],42)
            self.assertIsNone(result["dropped_iterations"])
            self.assertEqual(result["scheduling_evidence"],"not-applicable-closed-model")
            with self.assertRaises((ValueError,KeyError)):validate_export(p,"k6","matched","run")

    def test_closed_mode_missing_final_marker_or_summary_disagreement_rejected(self):
        for change in ("final","count"):
            with tempfile.TemporaryDirectory() as temp:
                p=Path(temp);self.fixture(p)
                if change=="final":
                    (p/"generator.log").write_text("\n".join((p/"generator.log").read_text().splitlines()[:-1])+"\n")
                else:
                    data=json.loads((p/"k6-summary.json").read_text());data["metrics"]["http_reqs"]["values"]["count"]=41;(p/"k6-summary.json").write_text(json.dumps(data))
                rehash(p)
                with self.assertRaises((ValueError,KeyError)):validate_export(p,"k6","idle-boundary","run")

    def test_idle_bounds_follow_last_actual_completion(self):
        self.assertTrue(hasattr(probe,"idle_window_bounds"),"idle bounds helper is missing")
        markers=[dict(started_at="2026-10-20T00:00:00Z"),dict(ended_at="2026-10-20T00:02:12Z")]
        ledger=[dict(completed_at="2026-10-20T00:02:10Z")]
        result=probe.idle_window_bounds(markers,ledger)
        self.assertEqual(result["end"],"2026-10-20T00:02:10Z")
        self.assertNotEqual(result["observation_seconds"],300)


    def test_closed_mode_present_dropped_metric_still_requires_granular_parity(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp);self.fixture(p)
            data=json.loads((p/"k6-summary.json").read_text());data["metrics"]["dropped_iterations"]={"values":{"count":1}}
            (p/"k6-summary.json").write_text(json.dumps(data));rehash(p)
            with self.assertRaises(ValueError):validate_export(p,"k6","idle-boundary","run")
            with (p/"k6.ndjson").open("a") as out:out.write(json.dumps(dict(type="Point",metric="dropped_iterations",data=dict(value=1)))+"\n")
            rehash(p)
            self.assertEqual(validate_export(p,"k6","idle-boundary","run")["dropped_iterations"],1)

    def test_closed_mode_duplicate_pair_phase_and_incomplete_iterations_rejected(self):
        for change in ("pair","iterations"):
            with tempfile.TemporaryDirectory() as temp:
                p=Path(temp);self.fixture(p)
                if change=="pair":
                    lines=(p/"generator.log").read_text().splitlines()
                    for n,line in enumerate(lines):
                        if line.startswith("EVAL_REQUEST "):
                            row=json.loads(line.split(" ",1)[1]);row["phase"]="probe";lines[n]="EVAL_REQUEST "+json.dumps(row);break
                    (p/"generator.log").write_text("\n".join(lines)+"\n")
                else:
                    data=json.loads((p/"k6-summary.json").read_text());data["metrics"]["iterations"]["values"]["count"]=20
                    (p/"k6-summary.json").write_text(json.dumps(data))
                rehash(p)
                with self.assertRaises(ValueError):validate_export(p,"k6","idle-boundary","run")

    def test_materialize_closed_request_attempt_and_window_events(self):
        from evaluation.generator_execution import _materialize
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);raw=root/"raw";self.fixture(raw);dest=root/"materialized";dest.mkdir()
            _materialize(raw,dest,"k6","idle-boundary")
            self.assertEqual(len((dest/"ledger.ndjson").read_text().splitlines()),42)
            self.assertEqual(len((dest/"attempts.ndjson").read_text().splitlines()),42)
            self.assertEqual(len(json.loads((dest/"generator-window.json").read_text())),2)

if __name__=="__main__":unittest.main()
