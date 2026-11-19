import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from evaluation.gke.bounded_lifecycle import acquire, graph_observation, bounds, append_record


class BoundedLifecycleTests(unittest.TestCase):
    def pod(self, name="new", uid="u-new"):
        return {"metadata":{"name":name,"namespace":"demo","uid":uid,"resourceVersion":"42"},
                "spec":{"nodeName":"node-b"},
                "status":{"phase":"Running","conditions":[{"type":"Ready","status":"True"}],
                          "containerStatuses":[{"name":"app","ready":True,"restartCount":0,"containerID":"containerd://x"}]}}

    def reference(self, name="new", uid="u-new", start=20, end=21):
        row={"pod":name,"uid":uid,"node":"node-b","health":"healthy"}
        return {"version":"bounded-lifecycle-v1","state":"observed","phase":"after",
                "acquisition":{"started_monotonic":start,"completed_monotonic":end,
                               "started_at":100+start,"completed_at":100+end},
                "replicas":[row],"pods":[self.pod(name,uid)]}

    def graph(self, name="new"):
        nodes=[{"id":"s","name":"checkoutservice","kind":"service","namespace":"demo"},
               {"id":"d","kind":"workload"},{"id":"rs","kind":"workload"},
               {"id":"p","name":name,"kind":"pod","namespace":"demo","status":"healthy"},
               {"id":"n","name":"node-b","kind":"node"}]
        edges=[{"source":"s","target":"d","relation":"exposes"},
               {"source":"d","target":"rs","relation":"contains"},
               {"source":"rs","target":"p","relation":"contains"},
               {"source":"p","target":"n","relation":"runs_on"}]
        return {"state":"observed","snapshot":{"id":2,"created_at":"2026-11-17T12:00:00Z"},
                "nodes":nodes,"edges":edges,"requests":[]}

    def observation(self):
        ref=self.reference()
        record=graph_observation(self.graph(),ref["replicas"],namespace="demo",service="checkoutservice",
                                 started_at=130,completed_at=132,started_monotonic=30,completed_monotonic=32)
        return ref,record

    def test_acquisition_keeps_uid_versions_and_container_states_without_env(self):
        from evaluation.gke.thesis import replica_inventory
        calls=[]
        def json_read(verb,kind,*args):
            calls.append(kind)
            if kind=="service":return {"spec":{"selector":{"app":"checkout"}}}
            return {"items":[dict(self.pod(),spec={"nodeName":"node-b","containers":[{"env":[{"value":"SECRET"}]}]})]}
        replicas, record=acquire(replica_inventory,SimpleNamespace(json=json_read),"demo","checkoutservice",phase="after")
        self.assertEqual(calls,["service","pods"])
        self.assertEqual(record["state"],"observed")
        self.assertEqual(record["pods"][0]["metadata"]["resourceVersion"],"42")
        self.assertEqual(record["pods"][0]["status"]["containerStatuses"][0]["containerID"],"containerd://x")
        self.assertNotIn("env",str(record))
        self.assertEqual(replicas,record["replicas"])

    def test_bounded_elapsed_is_not_an_exact_ingestion_lag(self):
        ref,poll=self.observation()
        result=bounds(self.reference("old","u-old",1,2),ref,{"started_monotonic":10,"completed_monotonic":11},[poll])
        self.assertEqual(result["state"],"pass",result)
        self.assertEqual(result["trigger_to_first_verified_snapshot_seconds"],[0,22])
        self.assertEqual(result["reference_to_api_acquisition_offset_seconds"],[9,12])
        self.assertFalse(result["exact_transition_time_established"])

    def test_names_alone_wrong_placement_health_or_scope_cannot_match(self):
        for change in ("node","health","service","snapshot"):
            graph=self.graph()
            if change=="node":graph["nodes"][-1]["name"]="wrong-node"
            elif change=="health":graph["nodes"][-2]["status"]="unknown"
            elif change=="service":graph["nodes"][0]["namespace"]="other"
            elif change=="snapshot":graph.pop("snapshot")
            row=graph_observation(graph,self.reference()["replicas"],namespace="demo",service="checkoutservice",
                                  started_at=130,completed_at=132,started_monotonic=30,completed_monotonic=32)
            self.assertFalse(row["matches_expected"],change)

    def test_missing_versions_same_uid_or_same_name_stay_insufficient(self):
        for change in ("version","uid","name","poll"):
            ref,poll=self.observation();old=self.reference("old","u-old",1,2)
            if change=="version":ref["pods"][0]["metadata"].pop("resourceVersion")
            elif change=="uid":old=self.reference("old","u-new",1,2)
            elif change=="name":old=self.reference("new","u-old",1,2)
            elif change=="poll":poll["matches_expected"]=False
            result=bounds(old,ref,{"started_monotonic":10,"completed_monotonic":11},[poll])
            self.assertEqual(result["state"],"insufficient",change)

    def test_failed_poll_is_retained_without_shifting_trigger(self):
        ref,poll=self.observation()
        failed=dict(poll,state="insufficient",matches_expected=False,reason="IAP timeout")
        result=bounds(self.reference("old","u-old",1,2),ref,
                      {"started_monotonic":10,"completed_monotonic":11},[failed,poll])
        self.assertEqual(result["state"],"pass")
        self.assertEqual(result["unavailable_polls"],1)
        self.assertEqual(result["trigger_to_first_verified_snapshot_seconds"],[0,22])

    def test_append_never_overwrites_and_reports_storage_bound(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"records.ndjson";append_record(p,{"a":1});original=p.read_bytes()
            with self.assertRaises(ValueError):append_record(p,{"a":"too large"},limit=len(original)+1)
            self.assertEqual(p.read_bytes(),original)
            append_record(p,{"a":2})
            self.assertEqual(len(p.read_text().splitlines()),2)


class BoundedHistoryIntegrationTests(unittest.TestCase):
    pod=BoundedLifecycleTests.pod
    graph=BoundedLifecycleTests.graph
    def test_official_history_retains_reference_and_public_api_acquisitions(self):
        import json
        import time
        from contextlib import nullcontext
        from unittest.mock import patch
        from evaluation.gke import thesis_history
        from evaluation.gke.models import write
        state={"new":False}
        old=self.pod("old","u-old");new=self.pod()
        deployment={"metadata":{"uid":"deployment","generation":1},
                    "spec":{"selector":{"matchLabels":{"app":"checkout"}}}}
        def json_read(verb,kind,*args):
            if kind=="deployment":return deployment
            if kind=="service":return {"spec":{"selector":{"app":"checkout"}}}
            return {"items":[new if state["new"] else old]}
        def restart(*args,**kwargs):
            state["new"]=True
            return {"requested_at":time.time(),"state":"requested"}
        def graph(*,at):
            return dict(self.graph("new" if state["new"] else "old"),
                        snapshot={"id":2 if state["new"] else 1,"created_at":at})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            ctx=SimpleNamespace(config={},manifest={"protocol_definition":{"lifecycle_observation_version":"bounded-lifecycle-v1"}},
                 json=json_read,kube=lambda *a,**kw:None,write=write,run_id="fixture")
            def consumer(*args,**kwargs):
                doc={"openapi_validated":True,"snapshot_changed":True,
                     "before":{"snapshot":json.loads((path/"snapshot-before.json").read_text())["snapshot"]},
                     "after":{"snapshot":json.loads((path/"snapshot-after.json").read_text())["snapshot"]},
                     "answers":{"before_replicas":{"state":"observed","value":[{"pod":"old","node":"node-b","health":"healthy"}]},
                                "after_replicas":{"state":"observed","value":[{"pod":"new","node":"node-b","health":"healthy"}]}}}
                write(path/"consumer/ledger.json",doc)
                return {"cleanup_verified":True,"valid":True,"job_completed":True}
            report={}
            with patch.object(thesis_history.r,"forward",return_value=nullcontext("http://fixture")), \
                 patch.object(thesis_history,"APIConsumer",return_value=SimpleNamespace(graph=graph)), \
                 patch.object(thesis_history,"restart_target",side_effect=restart), \
                 patch.object(thesis_history,"observe_restart",return_value=(deployment,{"items":[new]})), \
                 patch.object(thesis_history,"capture_consumer",side_effect=consumer):
                thesis_history._capture(ctx,SimpleNamespace(namespace="demo",source="mesh"),{},path,time.monotonic()+500,report)
            self.assertEqual(report["answer"]["state"],"correct",report)
            self.assertIn("lifecycle_observation",report)
            self.assertEqual(report["lifecycle_observation"]["state"],"pass",report)
            self.assertGreaterEqual(len((path/"lifecycle-reference.ndjson").read_text().splitlines()),3)
            self.assertGreaterEqual(len((path/"lifecycle-api.ndjson").read_text().splitlines()),2)
            self.assertTrue((path/"lifecycle-trigger.json").exists())


class OfficialBoundedProtocolTests(unittest.TestCase):
    def test_official_definition_enables_bounded_observation_without_extra_traffic(self):
        from evaluation.gke.models import read, build_matrix
        root=Path(__file__).resolve().parents[2]
        protocol=read(root/"protocols/thesis-completion-v2/protocol.yaml")
        self.assertEqual(protocol.get("lifecycle_observation_version"),"bounded-lifecycle-v1")
        self.assertEqual(protocol.get("traffic_population_version"),"settled-primary-v1")
        self.assertTrue(protocol.get("retain_fixed_window_diagnostic"))
        rows=build_matrix(protocol,schedule="thesis-completion-v2")
        self.assertEqual(len(rows),14)
        self.assertEqual(sum(r["duration_seconds"] for r in rows),2280)
        self.assertEqual(protocol["readiness_cases"],0)

    def test_settled_duration_is_not_reported_as_fixed_window(self):
        from evaluation.gke.thesis_comparison import duration_evidence
        report={"families":{"traffic_duration":{"state":"pass","population_scope":"settled"},
                            "settled_traffic_duration":{"state":"pass"}}}
        self.assertIn("settled",duration_evidence(report)["method"])
        self.assertNotIn("fixed-window",duration_evidence(report)["method"])

    def test_claim_requires_verified_history_and_reports_bound_separately(self):
        from evaluation.gke.thesis import claim_matrix
        ctx=SimpleNamespace(manifest={"schedule":"thesis-completion-v2","matrix":[],
            "product_pins":{"karthos_revision":"4c2861b"},
            "protocol_definition":{"lifecycle_observation_version":"bounded-lifecycle-v1"}},state={})
        for verified,answer,expected in ((True,"correct","pass"),(False,"correct","insufficient"),(True,"incorrect","insufficient")):
            history={"answer":{"state":answer},"continuation_safe":True,"export":{"verified":verified,"uri":"gs://fixture/history"},
                     "lifecycle_observation":{"state":"pass","version":"bounded-lifecycle-v1","exact_transition_time_established":False}}
            rows=claim_matrix(ctx,[],history,None)["claims"]
            matches=[r for r in rows if r["claim"]=="bounded-lifecycle-observation"]
            self.assertEqual(len(matches),1)
            self.assertEqual(matches[0]["verdict"],expected)
            self.assertIn("no exact",matches[0]["reference"])


class BoundedLifecycleIntegrityTests(unittest.TestCase):
    pod=BoundedLifecycleTests.pod
    graph=BoundedLifecycleTests.graph
    reference=BoundedLifecycleTests.reference
    observation=BoundedLifecycleTests.observation
    def test_source_readiness_and_api_identity_cannot_be_overridden_by_summary_flags(self):
        for mutation in ("source-ready", "api-placement", "namespace"):
            ref,poll=self.observation()
            if mutation=="source-ready":ref["pods"][0]["status"]["conditions"][0]["status"]="False"
            elif mutation=="api-placement":poll["replicas"][0]["node"]="wrong-node"
            elif mutation=="namespace":ref["namespace"]="other"
            result=bounds(self.reference("old","u-old",1,2),ref,
                          {"started_monotonic":10,"completed_monotonic":11},[poll])
            self.assertEqual(result["state"],"insufficient",mutation)
