"""Bounded Kubernetes/API lifecycle observations, not exact collector-cache lag."""
from __future__ import annotations
import copy
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

VERSION = "bounded-lifecycle-v1"
LEDGER_LIMIT = 16*1024*1024


def append_record(path, record, *, limit=LEDGER_LIMIT):
    path = Path(path)
    payload = (json.dumps(record, sort_keys=True, allow_nan=False)+"\n").encode()
    if (path.stat().st_size if path.exists() else 0)+len(payload) > limit:
        raise ValueError("bounded lifecycle evidence byte limit exceeded")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write(payload)
        stream.flush()


def _interval(record):
    a=record["acquisition"]
    start,end=a["started_monotonic"],a["completed_monotonic"]
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)) or end<start:
        raise ValueError("invalid acquisition interval")
    return start,end


def _reference(record):
    if record.get("version")!=VERSION or record.get("state")!="observed":
        raise ValueError("independent reference unavailable")
    _interval(record)
    rows=record["replicas"];pods=record["pods"]
    if not rows or len(rows)!=len({r["uid"] for r in rows}) or len(rows)!=len({r["pod"] for r in rows}):
        raise ValueError("empty or ambiguous independent replicas")
    by_uid={p["metadata"]["uid"]:p for p in pods}
    if len(by_uid)!=len(pods) or set(by_uid)!={r["uid"] for r in rows}:
        raise ValueError("reference Pod UID inventory differs")
    for row in rows:
        pod=by_uid[row["uid"]]
        status=pod.get("status",{})
        ready=[c.get("status") for c in status.get("conditions",[]) if c.get("type")=="Ready"]
        if status.get("phase")!="Running" or ready!=["True"]:
            raise ValueError("source Pod is not unambiguously Running and Ready")
        if record.get("namespace") is not None and pod["metadata"].get("namespace")!=record["namespace"]:
            raise ValueError("reference namespace differs")
        if (not pod["metadata"].get("resourceVersion") or pod["metadata"]["name"]!=row["pod"]
                or not row.get("node") or pod.get("spec",{}).get("nodeName")!=row["node"]
                or row.get("health")!="healthy"):
            raise ValueError("reference identity/version/healthy placement unavailable")
    return rows


def acquire(reader, ctx, namespace, service, *, phase):
    inputs=[]
    started_at=time.time();started_monotonic=time.monotonic()
    def json_read(*args,**kwargs):
        begin=time.time();begin_mono=time.monotonic()
        body=ctx.json(*args,**kwargs)
        finish_mono=time.monotonic();finish=time.time()
        if len(args)>1 and args[1]=="pods":
            pods=[]
            for pod in body.get("items",[]):
                meta=pod.get("metadata",{})
                if meta.get("deletionTimestamp"):continue
                pods.append({"metadata":{k:copy.deepcopy(meta[k]) for k in ("name","namespace","uid","resourceVersion") if k in meta},
                    "spec":{"nodeName":pod.get("spec",{}).get("nodeName")},
                    "status":{k:copy.deepcopy(pod.get("status",{}).get(k)) for k in
                              ("phase","conditions","containerStatuses","initContainerStatuses") if k in pod.get("status",{})}})
            inputs.append(dict(pods=pods,list_resource_version=body.get("metadata",{}).get("resourceVersion"),
                               acquisition=dict(started_at=begin,completed_at=finish,
                                                started_monotonic=begin_mono,completed_monotonic=finish_mono)))
        return body
    rows=reader(SimpleNamespace(json=json_read),namespace,service)
    record=dict(version=VERSION,state="observed",phase=phase,namespace=namespace,service=service,
                replicas=copy.deepcopy(rows),pods=inputs[-1]["pods"] if len(inputs)==1 else [],
                acquisition=inputs[-1]["acquisition"] if len(inputs)==1 else dict(
                    started_at=started_at,completed_at=time.time(),
                    started_monotonic=started_monotonic,completed_monotonic=time.monotonic()),
                list_resource_version=inputs[-1].get("list_resource_version") if len(inputs)==1 else None,
                source="independent Kubernetes API response; not kernel or collector-cache measurement")
    try:
        _reference(record)
        if any(p["metadata"].get("namespace")!=namespace for p in record["pods"]):
            raise ValueError("reference namespace differs")
    except (KeyError,TypeError,ValueError) as exc:
        record.update(state="insufficient",reason=str(exc))
    return rows,record


def graph_observation(graph, expected, *, namespace, service, started_at, completed_at,
                      started_monotonic, completed_monotonic):
    record=dict(version=VERSION,state=graph.get("state","insufficient"),matches_expected=False,
                namespace=namespace,service=service,snapshot=copy.deepcopy(graph.get("snapshot")),
                acquisition=dict(started_at=started_at,completed_at=completed_at,
                                 started_monotonic=started_monotonic,completed_monotonic=completed_monotonic),
                requests=copy.deepcopy(graph.get("requests",[])))
    try:
        _interval(record)
        if graph.get("state")!="observed":raise ValueError(graph.get("reason","public graph unavailable"))
        if not graph.get("snapshot",{}).get("id") or not graph["snapshot"].get("created_at"):
            raise ValueError("snapshot identity unavailable")
        from ..consumers.api_only import answer_questions
        facts=answer_questions(graph,[],namespace=namespace,service=service)
        placements=facts["node_placements"]
        if placements["state"]!="observed":raise ValueError("service-to-Pod placement association unavailable")
        nodes={n["id"]:n for n in graph["nodes"]}
        rows=sorted((dict(pod=nodes[p["pod"]]["name"],node=nodes[p["node"]]["name"],
                          health=nodes[p["pod"]].get("status","unknown"))
                     for p in placements["observed"]),key=lambda r:r["pod"])
        expected_rows=sorted(({k:r[k] for k in ("pod","node","health")} for r in expected),key=lambda r:r["pod"])
        record.update(replicas=rows,matches_expected=bool(expected_rows) and rows==expected_rows)
    except (KeyError,TypeError,ValueError) as exc:
        record.update(state="insufficient",reason=str(exc))
    return record


def bounds(before, after, trigger, polls):
    result=dict(version=VERSION,state="insufficient",exact_transition_time_established=False,
                scope="controller-observed elapsed bounds; not exact ingestion/cache or kernel measurement lag",
                polls=len(polls),unavailable_polls=sum(p.get("state")!="observed" for p in polls))
    try:
        old,new=_reference(before),_reference(after)
        if {r["uid"] for r in old}&{r["uid"] for r in new}:
            raise ValueError("replacement UID set is not disjoint")
        if {r["pod"] for r in old}&{r["pod"] for r in new}:
            raise ValueError("same-name replacement cannot be matched to runtime UID through this API")
        start,end=trigger["started_monotonic"],trigger["completed_monotonic"]
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)) or end<start:
            raise ValueError("invalid trigger interval")
        if _interval(before)[1]>start or _interval(after)[0]<end:
            raise ValueError("reference acquisitions do not bracket restart request")
        matching=[p for p in polls if p.get("state")=="observed" and p.get("matches_expected") is True]
        if not matching:raise ValueError("no matching healthy placed replacement snapshot within observation budget")
        first=matching[0];a,b=_interval(first);c,d=_interval(after)
        expected=sorted(({k:r[k] for k in ("pod","node","health")} for r in new),key=lambda r:r["pod"])
        if sorted(first.get("replicas",[]),key=lambda r:r["pod"])!=expected:
            raise ValueError("matching API replicas differ from independently observed placement/health")

        if a<end:raise ValueError("matching API acquisition predates completed trigger")
        result.update(state="pass",trigger_to_first_verified_snapshot_seconds=[0,b-start],
                      reference_to_api_acquisition_offset_seconds=[a-d,b-c],
                      first_matching_api_acquisition=first["acquisition"],first_matching_snapshot=first["snapshot"],
                      independent_reference_acquisition=after["acquisition"],
                      observation_start_is_not_transition_time=True,
                      limitation="upper bound includes rollout, materialization, polling and transport; their individual contributions are not established")
    except (KeyError,TypeError,ValueError) as exc:
        result["reason"]=str(exc)
    return result
