import copy
import json
import unittest

from evaluation.gke.trace_settled import check


class TraceSettledTests(unittest.TestCase):
    def fixture(self):
        from evaluation.tests.test_offline_population import PopulationTests
        t, v = PopulationTests().fixture()
        t["manifest"]["source_id"] = "traces.servicegraph"
        trace = dict(identity="a/b", trace_id="a"*32, target_service="checkoutservice",
                     start=12, completion=13, duration_ms=20, paired_frontend=True,
                     conflicting_identity=False, connector_failed=False)
        t["events"]["traces"] = [trace]
        t["events"]["observers"][0]["traceparent"] = "00-"+"a"*32+"-"+"b"*16+"-01"
        t["whole_capture_source_identity_coverage"] = dict(checkout_trace_ids=["a"*32], paired_trace_ids=["a"*32])
        t["generator_contract"] = dict(checkout_request_ids=["r"])
        labels = dict(__name__="servicegraph_request_count", edge_client="frontend",
                      edge_server="checkoutservice", client_namespace="app",
                      server_namespace="app", source="traces.servicegraph", failed="false",
                      karthos_unit="count")
        row = dict(metric=labels, first=8, last=23, increase=1, resets=0)
        rows = [row, dict(row, metric=dict(labels, __name__="servicegraph_request_duration_count")),
                dict(row, metric=dict(labels, __name__="servicegraph_request_duration_sum",
                                      karthos_unit="seconds"), increase=0.020)]
        key = json.dumps(labels, sort_keys=True)
        def quiet(at, value):
            return dict(stable=True, measured_collection_interval_seconds=1,
                        records=[dict(observed_at=at+i, values={key:value},
                                      sample_times={key:at}) for i in (0,1)])
        return t, v, copy.deepcopy(rows), copy.deepcopy(rows), quiet(8, 10), quiet(23, 11)

    def run_check(self, t, v, native, stored, before, after):
        return check(t, dict(vm=dict(series=stored)), native, before, after, {},
                     fixed_verification=v)

    def test_settled_pass_keeps_original_fixed_diagnostic_and_inputs(self):
        args=self.fixture(); original=copy.deepcopy(args)
        result=self.run_check(*args)
        self.assertEqual(result["state"], "pass", result)
        self.assertEqual(result["requests"]["observed"], 1)
        self.assertEqual(result["errors"]["observed"], 0)
        self.assertEqual(result["duration"]["stored_mean_ms"], 20)
        self.assertEqual(result["fixed_window"]["state"], "insufficient")
        self.assertEqual(args, original)

    def test_missing_evidence_is_not_zero(self):
        for mutation in ("unstable", "unpaired", "reset", "extra", "duplicate",
                         "missing_sum", "wrong_unit", "wrong_source", "wrong_endpoint",
                         "quiet_disagreement", "unknown_dimension", "missing_tail", "count_unit", "canonical_id"):
            with self.subTest(mutation=mutation):
                t,v,n,s,b,a=self.fixture()
                if mutation=="unstable": b["stable"]=False
                elif mutation=="unpaired": t["events"]["traces"][0]["paired_frontend"]=False
                elif mutation=="reset": s[0]["resets"]=1
                elif mutation=="extra": t["events"]["observers"].append(dict(t["events"]["observers"][0],identity="extra",start=21,completion=22))
                elif mutation=="duplicate": n.append(copy.deepcopy(n[0]))
                elif mutation=="missing_sum": s.pop()
                elif mutation=="wrong_unit": s[-1]["metric"]["karthos_unit"]="milliseconds"
                elif mutation=="wrong_source": n[0]["metric"]["source"]="mesh.istio"
                elif mutation=="wrong_endpoint": s[-1]["last"]=24
                elif mutation=="quiet_disagreement": a["records"][-1]["values"][next(iter(a["records"][-1]["values"]))]=12
                elif mutation=="unknown_dimension": s[0]["metric"]["unknown"]="x"
                elif mutation=="missing_tail": s.pop(1)
                elif mutation=="count_unit": s[0]["metric"]["karthos_unit"]="ratio"
                elif mutation=="canonical_id": s[-1]["metric"]["edge_server_id"]="wrong"
                self.assertEqual(self.run_check(t,v,n,s,b,a)["state"], "insufficient")

    def test_duration_disagreement_remains_a_failure(self):
        t,v,n,s,b,a=self.fixture();s[-1]["increase"]=1
        result=self.run_check(t,v,n,s,b,a)
        self.assertEqual(result["state"], "fail", result)
        self.assertEqual(result["duration"]["state"], "fail")

    def test_failed_partition_is_not_http_status_policy(self):
        t,v,n,s,b,a=self.fixture()
        t["events"]["traces"][0]["connector_failed"]=True
        for rows in (n,s):
            for row in rows: row["metric"]["failed"]="true"
        for doc in (b,a):
            for record in doc["records"]:
                old=next(iter(record["values"]));label=json.loads(old);label["failed"]="true"
                new=json.dumps(label,sort_keys=True)
                record["values"]={new:record["values"][old]}
                record["sample_times"]={new:record["sample_times"][old]}
        result=self.run_check(t,v,n,s,b,a)
        self.assertEqual(result["state"], "pass", result)
        self.assertEqual(result["errors"]["observed"],1)


class TraceSettledParserTests(TraceSettledTests):
    def evidence(self):
        t,v,n,s,b,a=self.fixture()
        native=[]
        for row in n:
            canonical=row["metric"]["__name__"]
            hist="duration" in canonical
            if canonical.endswith("_sum"):
                continue
            name="traces_service_graph_request_server" if hist else "traces_service_graph_request_total"
            points=[]
            attrs={"client":"frontend","server":"checkoutservice","client_k8s.namespace.name":"app",
                   "server_k8s.namespace.name":"app","karthos_source":"traces.servicegraph",
                   "failed":False,"connection_type":""}
            attributes=[dict(key=k,value={("boolValue" if type(value) is bool else "stringValue"):value}) for k,value in attrs.items()]
            for at,count,total in ((8,10,0.2),(23,11,0.22)):
                point=dict(attributes=attributes,timeUnixNano=str(at*10**9),startTimeUnixNano="1000000000")
                point.update(dict(count=str(count),sum=total) if hist else dict(asInt=str(count)))
                points.append(point)
            kind="histogram" if hist else "sum"
            contract=dict(aggregationTemporality=2,dataPoints=points)
            if not hist: contract["isMonotonic"]=True
            native.append(dict(name=name,unit="s" if hist else "",**{kind:contract}))
        raw=[dict(resourceMetrics=[dict(scopeMetrics=[dict(metrics=native)])])]
        vm=[]
        for row in s:
            base=0.2 if row["metric"]["__name__"].endswith("_sum") else 10
            vm.append(dict(metric=row["metric"],timestamps=[8000,23000],values=[base,base+row["increase"]]))
        docs={"structured/gateway/raw/metrics.json":raw,"vm-raw.ndjson":vm}
        return t,v,b,a,docs

    def parsed(self,t,v,b,a,docs):
        from evaluation.gke.trace_settled import from_evidence
        return from_evidence(t,v,list(docs),lambda name:{"quiet-before.json":b,"quiet-after.json":a}[name],
                             lambda name:iter(docs[name]),{})

    def test_raw_parser_and_recorded_endpoints(self):
        args=self.evidence();r=self.parsed(*args)
        self.assertEqual(r["state"],"pass",r)
        self.assertEqual(r["recorded_quiet_bounds"],[8,23])

    def test_parser_never_infers_temporality_or_accepts_conflicts(self):
        for mutation in ("temporality","monotonic","unit","conflict","epoch","tail","length","extra_dimension","error_missing"):
            with self.subTest(mutation=mutation):
                t,v,b,a,docs=self.evidence()
                metrics=docs["structured/gateway/raw/metrics.json"][0]["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
                counter=metrics[0]["sum"]
                if mutation=="temporality": counter.pop("aggregationTemporality")
                elif mutation=="monotonic": counter.pop("isMonotonic")
                elif mutation=="unit": metrics[1]["unit"]="ms"
                elif mutation=="conflict": counter["dataPoints"].append(dict(counter["dataPoints"][-1],asInt="12"))
                elif mutation=="epoch": counter["dataPoints"][-1]["startTimeUnixNano"]="2000000000"
                elif mutation=="tail": docs["vm-raw.ndjson"][-1]["timestamps"][-1]=22000
                elif mutation=="length": docs["vm-raw.ndjson"][0]["values"].pop()
                elif mutation=="extra_dimension": counter["dataPoints"][0]["attributes"].append(dict(key="unmapped",value={"stringValue":"x"}))
                elif mutation=="error_missing":
                    # Outcome changed to failed but no connector-native failure counter supplied.
                    t["events"]["traces"][0]["connector_failed"]=True
                    for row in docs["vm-raw.ndjson"]: row["metric"]["failed"]="true"
                    for metric in metrics:
                        for point in metric.get("sum",metric.get("histogram"))["dataPoints"]:
                            next(x for x in point["attributes"] if x["key"]=="failed")["value"]={"boolValue":True}
                    for doc in (b,a):
                        for record in doc["records"]:
                            old=next(iter(record["values"]));label=json.loads(old);label["failed"]="true";new=json.dumps(label,sort_keys=True)
                            record["values"]={new:record["values"][old]};record["sample_times"]={new:record["sample_times"][old]}
                self.assertEqual(self.parsed(t,v,b,a,docs)["state"],"insufficient",mutation)

    def test_report_keeps_fixed_and_settled_families(self):
        from evaluation.gke.report_summary import family_summaries
        r=self.parsed(*self.evidence())
        report={"settled_trace_population":r}
        families=family_summaries(report)
        self.assertEqual(families["traffic_duration"]["state"],"pass")
        self.assertEqual(families["fixed_window_traffic_duration"]["state"],"insufficient")
        self.assertEqual(families["settled_traffic_count"]["state"],"pass")
