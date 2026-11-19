"""Settled trace population accounting at recorded quiet boundaries.

This is aggregate evidence, not per-request storage identity or a replacement
for the original fixed-window API query. No workload boundary is changed.
"""
from __future__ import annotations
import math

from .population import _key, prove
from ..scoring import DEFAULTS, compare, stamp
from ..timing_analysis import _quiet_endpoint

VERSION = "trace-settled-population-v2"


def partition_endpoints(before, after, source):
    """Bind each semantic partition to its own recorded measurement times."""
    import json
    endpoints=[]
    for document in (before,after):
        values,reasons=_quiet_endpoint(document,source)
        if reasons:raise ValueError('; '.join(reasons))
        times=document['records'][-1]['sample_times'];projected={}
        for identity in values:
            labels=json.loads(identity);key=_key(labels,source)
            if labels.get('__name__')!='servicegraph_request_count' or key in projected:
                raise ValueError('unexpected or ambiguous quiet partition')
            projected[key]=stamp(times[identity])
        endpoints.append(projected)
    if set(endpoints[0])!=set(endpoints[1]):raise ValueError('quiet outcome partition inventory changed')
    return {key:(left,endpoints[1][key]) for key,left in endpoints[0].items()}



def series_quiet_bounds(points, before, after):
    """Latest measurement before each quiet observation; values never pick times."""
    by_time={}
    for at,value in points:
        if not math.isfinite(at) or not math.isfinite(value):raise ValueError('nonfinite series sample')
        if at in by_time and by_time[at]!=value:raise ValueError('conflicting series samples')
        by_time[at]=value
    bounds=[];proof=[]
    for document in (before,after):
        chosen=[]
        for record in document['records'][-2:]:
            observed=stamp(record['observed_at'])
            at=max((at for at in by_time if at<=observed),default=None)
            if at is None or observed-at>60:raise ValueError('missing or stale quiet series measurement')
            chosen.append(dict(observed_at=observed,at=at,value=by_time[at]))
        if len(chosen)!=2 or chosen[0]['at']>=chosen[1]['at'] or chosen[0]['value']!=chosen[1]['value']:
            raise ValueError('series quiet stability lacks distinct repeated measurements')
        bounds.append(chosen[-1]['at']);proof.append(chosen)
    return tuple(bounds),proof


def check(traffic, verification, native_series, before, after, thresholds,
          *, fixed_verification=None):
    result = dict(version=VERSION, state="insufficient",
                  scope="paired checkout population at recorded quiet counter endpoints",
                  fixed_workload_bounds=traffic.get("windows", {}).get("whole", {}).get("bounds_seconds"),
                  fixed_window=prove("servicegraph.request.duration", traffic,
                                     fixed_verification if fixed_verification is not None else verification),
                  individual_stored_request_ids_supported=False)
    try:
        source = traffic.get("manifest", {}).get("source_id")
        if source != "traces.servicegraph":
            return dict(result, state="not-applicable", reason="not the selected trace connector source")
        per_partition = partition_endpoints(before, after, source)
        endpoints = [min(pair[0] for pair in per_partition.values()),max(pair[1] for pair in per_partition.values())]
        quiet_partitions = []
        for doc in (before, after):
            values, reasons = _quiet_endpoint(doc, source)
            if reasons:
                raise ValueError("; ".join(reasons))
            projected = {}
            import json
            for identity, value in values.items():
                labels = json.loads(identity)
                if labels.get("__name__") != "servicegraph_request_count":
                    raise ValueError("unexpected quiet counter metric")
                key = _key(labels, source)
                if key in projected:
                    raise ValueError("ambiguous quiet partitions")
                if float(value) < 0:
                    raise ValueError("negative quiet counter")
                projected[key] = float(value)
            quiet_partitions.append(projected)
        lo, hi = endpoints
        start, end = result["fixed_workload_bounds"]
        if any(not left < start < end < right for left,right in per_partition.values()):
            raise ValueError("quiet endpoints do not enclose the original workload")
        if set(quiet_partitions[0]) != set(quiet_partitions[1]):
            raise ValueError("quiet outcome partition inventory changed")
        expected_quiet = {k: quiet_partitions[1][k]-v for k,v in quiet_partitions[0].items()}
        if any(v < 0 for v in expected_quiet.values()):
            raise ValueError("quiet counter reset")

        proofs = {}
        series_bounds = {}
        for boundary, rows in (("source", native_series),
                               ("storage", verification.get("vm", {}).get("series", []))):
            proof = prove("servicegraph.request.duration", traffic, {"vm": {"series": rows}})
            proofs[boundary] = proof
            if proof["state"] != "pass":
                result["population_proofs"] = proofs
                raise ValueError(boundary + ": " + proof.get("reason", "population unavailable"))
            request_rows = [r for r in rows if r.get("metric", {}).get("__name__") == "servicegraph_request_count"]
            actual = {_key(r["metric"], source): r["increase"] for r in request_rows}
            if actual != expected_quiet:
                raise ValueError(boundary + " counters differ from recorded quiet deltas")
            request_ids = {_key(r["metric"], source): tuple(r["metric"].get(k) for k in ("entity_id","edge_client_id","edge_server_id")) for r in request_rows}
            for row in rows:
                labels = row["metric"]
                if labels.get("__name__") in ("servicegraph_request_count","servicegraph_request_duration_count") and labels.get("karthos_unit") != "count":
                    raise ValueError("request/histogram count unit is not count")
                if tuple(labels.get(k) for k in ("entity_id","edge_client_id","edge_server_id")) != request_ids.get(_key(labels, source)):
                    raise ValueError("stored histogram canonical identity differs from request partition")
                if row["metric"].get("source") != source:
                    raise ValueError("wrong source in settled series")
                left,right=per_partition[_key(labels,source)]
                actual_bounds=(round(row["first"]*1000), round(row["last"]*1000))
                if labels['__name__']!='servicegraph_request_count' and row.get('quiet_series_proof'):
                    proof=row['quiet_series_proof']
                    if len(proof)!=2 or any(len(pair)!=2 or pair[0]['value']!=pair[1]['value'] or pair[0]['at']>=pair[1]['at'] or any(not 0<=r['observed_at']-r['at']<=60 or not math.isfinite(r['value']) for r in pair) for pair in proof):
                        raise ValueError('invalid repeated series quiet proof')
                    if actual_bounds!=tuple(round(pair[-1]['at']*1000) for pair in proof):
                        raise ValueError('series bounds differ from quiet proof')
                    if not row['first']<start<end<row['last']:
                        raise ValueError('series quiet bounds do not enclose workload')
                elif actual_bounds != (round(left*1000), round(right*1000)):
                    raise ValueError("series does not cover exact recorded quiet endpoints")
                identity=(labels['__name__'],_key(labels,source))
                if boundary=='source':series_bounds[identity]=actual_bounds
                elif series_bounds.get(identity)!=actual_bounds:
                    raise ValueError('source/storage series quiet bounds differ')
        result["population_proofs"] = proofs
        result["recorded_quiet_bounds"] = endpoints
        result["partition_endpoints"] = [dict(identity=dict(key),bounds=list(bounds)) for key,bounds in sorted(per_partition.items())]
        reference = proofs["storage"]["independent_reference"]
        selected = [t for t in traffic["events"]["traces"]
                    if t.get("trace_id") in set(reference["trace_ids"])]
        if any(type(t.get("duration_ms")) not in (int, float)
               or not math.isfinite(t["duration_ms"]) or t["duration_ms"] < 0 for t in selected):
            raise ValueError("missing or invalid paired server-span durations")

        def histogram(rows):
            sums = {}; counts = {}
            for row in rows:
                labels = row["metric"]; name = labels.get("__name__")
                target = sums if name == "servicegraph_request_duration_sum" else counts if name == "servicegraph_request_duration_count" else None
                if target is None:
                    continue
                key = _key(labels, source)
                if key in target or row.get("resets") != 0:
                    raise ValueError("ambiguous histogram partition or reset")
                if not isinstance(row.get("increase"), (int, float)) or not math.isfinite(row["increase"]) or row["increase"] < 0:
                    raise ValueError("missing or invalid histogram measurement")
                if target is sums and labels.get("karthos_unit") != "seconds":
                    raise ValueError("histogram sum unit is not seconds")
                target[key] = row
            if not sums or set(sums) != set(counts):
                raise ValueError("missing matching histogram sums/counts")
            if any((r["first"], r["last"]) != (counts[k]["first"], counts[k]["last"]) for k,r in sums.items()):
                raise ValueError("histogram sum/count brackets differ")
            count = sum(r["increase"] for r in counts.values())
            if count != reference["requests"] or count <= 0:
                raise ValueError("histogram population differs from paired checkout population")
            return sum(r["increase"] for r in sums.values())*1000/count, count

        native_mean, count = histogram(native_series)
        stored_mean, _ = histogram(verification["vm"]["series"])
        paired_mean = sum(t["duration_ms"] for t in selected)/len(selected)
        rel = thresholds.get("duration_relative_tolerance", DEFAULTS["duration_relative_tolerance"])
        absolute = thresholds.get("duration_absolute_tolerance_ms", DEFAULTS["duration_absolute_tolerance_ms"])
        duration_checks = [compare(paired_mean, native_mean, rel, absolute),
                           compare(native_mean, stored_mean, rel, absolute)]
        duration = dict(state="fail" if any(c["state"] == "fail" for c in duration_checks) else "pass",
                        paired_server_mean_ms=paired_mean, source_mean_ms=native_mean,
                        stored_mean_ms=stored_mean, count=count, checks=duration_checks)
        result.update(state=duration["state"], requests=dict(state="pass", expected=reference["requests"],
                      observed=sum(expected_quiet.values())),
                      errors=dict(state="pass", expected=reference["errors"],
                      observed=sum(v for k,v in expected_quiet.items() if dict(k).get("failed") == "true")),
                      duration=duration,
                      limitation="controlled aggregate population; cannot exclude compensating loss/duplication or prove exact API event-time membership")
        return result
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return dict(result, reason=str(exc))


def from_evidence(traffic, verification, names, document, records, thresholds):
    """Read source and stored samples without searching for agreeable endpoints."""
    import json
    from collections import defaultdict
    from ..evidence_v4 import attrs
    from ..scoring import counter_window
    source = traffic.get("manifest", {}).get("source_id")
    if source != "traces.servicegraph":
        return dict(version=VERSION, state="not-applicable", reason="not the selected trace connector source")
    try:
        before, after = document("quiet-before.json"), document("quiet-after.json")
        for doc in (before, after):
            _, errors = _quiet_endpoint(doc, source)
            if errors:
                raise ValueError("; ".join(errors))
        lo = min(stamp(t) for t in before["records"][-1]["sample_times"].values())
        hi = max(stamp(t) for t in after["records"][-1]["sample_times"].values())
        per_partition = partition_endpoints(before, after, source)
        namespace = traffic["manifest"]["namespace"]
        target = {"boutique":"checkoutservice", "astronomy":"checkout"}[traffic["manifest"]["demo"]]
        native = defaultdict(list); stored = defaultdict(list); starts = defaultdict(set)
        native_errors = defaultdict(list)
        mapping = {"traces_service_graph_request_total": "servicegraph_request_count",
                   "traces_service_graph_request_server": "servicegraph_request_duration"}
        allowed = {"client", "server", "client_k8s.namespace.name",
                   "server_k8s.namespace.name", "karthos_source", "failed", "connection_type"}
        for name in names:
            if name.startswith("structured/gateway/raw/") and name.endswith((".json", ".json.gz")):
                for doc in records(name):
                    for resource in doc.get("resourceMetrics", []):
                        for scope in resource.get("scopeMetrics", []):
                            for metric in scope.get("metrics", []):
                                native_name = metric.get("name")
                                if native_name not in mapping and native_name != "traces_service_graph_request_failed_total":
                                    continue
                                kind = "histogram" if native_name.endswith("_server") else "sum"
                                for point in metric.get(kind, {}).get("dataPoints", []):
                                    labels = attrs(point.get("attributes", []))
                                    if labels.get("client") != "frontend" or labels.get("server") != target:
                                        continue
                                    if labels.get("client_k8s.namespace.name") != namespace or labels.get("server_k8s.namespace.name") != namespace:
                                        continue
                                    if set(labels)-allowed or labels.get("connection_type") not in (None, ""):
                                        raise ValueError("unmapped native semantic dimensions")
                                    if labels.get("karthos_source") != source or type(labels.get("failed")) is not bool:
                                        raise ValueError("missing native source or outcome")
                                    contract = metric[kind]
                                    if contract.get("aggregationTemporality") not in (2, "AGGREGATION_TEMPORALITY_CUMULATIVE"):
                                        raise ValueError("native temporality is not explicitly cumulative")
                                    if kind == "sum" and contract.get("isMonotonic") is not True:
                                        raise ValueError("native request counter is not monotonic")
                                    if kind == "histogram" and metric.get("unit") != "s":
                                        raise ValueError("native duration unit is not seconds")
                                    # VictoriaMetrics stores OTLP times at millisecond resolution.
                                    # Raw nanosecond timestamps remain unchanged in the archive.
                                    at = int(point["timeUnixNano"])//1000000/1000
                                    base = dict(edge_client="frontend", edge_server=target,
                                                client_namespace=namespace, server_namespace=namespace,
                                                source=source, failed=str(labels["failed"]).lower())
                                    key = json.dumps(base, sort_keys=True)
                                    if stamp(before['records'][-2]['observed_at'])-60 <= at <= stamp(after['records'][-1]['observed_at']):
                                        starts[(native_name,key)].add(point.get("startTimeUnixNano"))
                                    if native_name == "traces_service_graph_request_failed_total":
                                        value = point.get("asInt", point.get("asDouble"))
                                        if value is None: raise ValueError("missing native error measurement")
                                        native_errors[key].append((at,float(value)))
                                    elif kind == "sum":
                                        value = point.get("asInt", point.get("asDouble"))
                                        if value is None: raise ValueError("missing native count measurement")
                                        native[("servicegraph_request_count",key,"count")].append((at,float(value)))
                                    else:
                                        if "count" not in point or "sum" not in point:
                                            raise ValueError("missing native histogram count or sum")
                                        for suffix, unit in (("count","count"),("sum","seconds")):
                                            native[("servicegraph_request_duration_"+suffix,key,unit)].append((at,float(point[suffix])))
            elif name in ("vm-raw.ndjson", "vm-raw.ndjson.gz"):
                for row in records(name):
                    labels = row.get("metric", {})
                    if (labels.get("source"),labels.get("edge_client"),labels.get("edge_server"),
                        labels.get("client_namespace"),labels.get("server_namespace")) != (source,"frontend",target,namespace,namespace):
                        continue
                    if labels.get("__name__") not in ("servicegraph_request_count","servicegraph_request_duration_count","servicegraph_request_duration_sum"):
                        continue
                    if len(row.get("timestamps",[])) != len(row.get("values",[])):
                        raise ValueError("stored sample arrays differ in length")
                    stored[json.dumps(labels,sort_keys=True)].extend((at/1000,float(v)) for at,v in zip(row["timestamps"],row["values"]))
        if any(None in values or len(values) != 1 for values in starts.values()):
            raise ValueError("native cumulative epoch missing or changed")
        def series_row(labels,points):
            bounds=per_partition[_key(labels,source)];proof=None
            if labels['__name__']!='servicegraph_request_count' and not all(any(at==bound for at,_ in points) for bound in bounds):
                bounds,proof=series_quiet_bounds(points,before,after)
            row=dict(metric=labels,**counter_window(points,*bounds,alignment=0))
            if proof:row['quiet_series_proof']=proof
            return row
        native_rows = [series_row(dict(json.loads(key),__name__=metric,karthos_unit=unit),points)
                       for (metric,key,unit),points in native.items()]
        stored_rows = [series_row(json.loads(key),points) for key,points in stored.items()]
        result = check(traffic, {"vm":{"series":stored_rows}}, native_rows, before, after,
                       thresholds, fixed_verification=verification)
        # Connector-native failures are observed directly, not reclassified from HTTP/gRPC.
        errors = [dict(identity=json.loads(key),**counter_window(points,*per_partition[_key(json.loads(key),source)],alignment=0))
                  for key,points in native_errors.items()]
        expected_errors = {json.dumps({k:v for k,v in r["metric"].items() if k not in ("__name__","karthos_unit")},sort_keys=True):r["increase"]
                           for r in native_rows if r["metric"]["__name__"]=="servicegraph_request_count" and r["metric"]["failed"]=="true"}
        actual_errors = {json.dumps(r["identity"],sort_keys=True):r["increase"] for r in errors}
        if (any(r.get("resets") != 0 or r.get("increase") is None for r in errors)
                or actual_errors != expected_errors):
            result.update(state="insufficient", reason="connector-native failed counter partitions disagree or are missing")
        result.update(native_series=native_rows,stored_series=stored_rows,native_failed_series=errors,
                      endpoint_rule="per-partition recorded quiet sample timestamps, exact millisecond identity; no search for agreeing values",
                      fixed_window_preserved=True)
        return result
    except (KeyError, TypeError, ValueError) as exc:
        return dict(version=VERSION, state="insufficient", reason="settled trace evidence unavailable: "+str(exc),
                    fixed_window_preserved=True)
