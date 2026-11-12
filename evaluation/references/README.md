# Independent GKE references and public API consumer

Offline-tested preparation only. No GKE acquisition or full live qualification has
been performed by these modules. Call per node for the three-node design; never
merge nodes or lifecycles into one reference. Missing source fields stay missing.
Generated CPU targets and payload byte counts are never reference measurements.

| Function | Input | Output |
|---|---|---|
| `kubelet.acquire_summary(node, runner)` | Explicit injected `evaluation.runner.kube` compatible command | Validated summary, successful request/receive times, failed attempts and bounded recovery metadata |
| `kubelet.read_summary(summary, node=, requested_at=, received_at=)` | Summary body and acquisition interval in epoch seconds | Source-value records with node, pod UID, pod/container start times, source measurement timestamp, dimensions, units |
| `host.read_proc(texts, node=, requested_at=, received_at=, boot_id=)` | Text keyed by `stat`, `meminfo`, `diskstats`, `net/dev` | CPU state ticks, original memory units, device/direction sectors and interface/direction bytes |
| `host.acquire_proc(node=, read_text=, clock=)` | Injected read-only host text reader | Same records, actual acquisition bracket and boot ID |
| `host.counter_delta(before, after)` | One uniquely matched monotonic source series at two disjoint actual acquisition brackets | Observed delta and conservative elapsed/rate bounds, or insufficient for missing/reset/lifecycle/timing ambiguity or cached source-time counters |
| `filesystem.acquire_owned(mounts, node=, statvfs=, clock=)` | Explicit `{mount, device, owned: true}` list | Read-only statvfs capacity/free/available/used bytes with exact mount/device dimensions |
| `filesystem.compare_static(reference, observed, absolute_tolerance=0)` | Values already selected for the same node, metric, device and mount | match/different/insufficient |
| `APIConsumer(endpoint).observe(before, after, queries=[])` | Public HTTP endpoint, RFC3339 `at` instants and public metric query dictionaries | Paged graph observations pinned to one snapshot each, changed snapshot ID, raw metric responses and acquisition times |
| `answer_questions(graph, metrics, service='checkout')` | Consumer observations only | Explicit callers, visible pod count, placements, requested resource/traffic samples; insufficient when unavailable |

Reference identity is deliberately separate from product normalization. CPU stays
nanocores/core-nanoseconds or proc ticks; memory kB stays kB; disk sectors stay
sectors. Do not apply an OTel conversion merely because a name resembles a
canonical metric. For host CPU totals, Linux guest ticks overlap user/nice; do not
sum all states blindly. Diskstats sector size semantics require upstream evidence
before conversion. Counter helpers expect monotonic counters, not sampled CPU
nanocore gauges, and a unique series selection. Source sample times may predate
acquisition (kubelet caching); compare cached counters by source timestamps rather
than passing them blindly to an acquisition-bracket rate comparison.

Frozen upstream evidence inspected (historical v0.156.0, not proof of a future GKE
image): `results/root-cause-20261020/upstream/kubelet_{cpu,network,fs}.go`.
[CPU implementation](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/kubeletstatsreceiver/internal/kubelet/cpu.go)
divides nano values by 1e9; [network implementation](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/kubeletstatsreceiver/internal/kubelet/network.go)
retains directional bytes and supports a selected default interface or all
interfaces; [filesystem implementation](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/kubeletstatsreceiver/internal/kubelet/fs.go)
records available/capacity/used separately. These readers perform none of those
OTel conversions. Integrators must freeze actual deployed image/version/config,
metric type, temporality, defaults and units before deriving agreement claims.

The bounded kubelet adapter reuses `FastCapture._read_kubelet_json` by making
an isolated function with injected runner globals; it does not monkeypatch shared
module state or mutate existing capture code. Its legacy first attempt allows
five seconds; cadence bounds retries, not a one-second hard timeout.

The API consumer uses only Python standard-library HTTP and JSON. It needs no
Kubernetes/DB/GCS credentials or mounts; no SDK or product module is imported.
Endpoint credentials, query strings and redirects are rejected. Graph pages must
carry the same snapshot metadata. Edges are selected at the node snapshot's
creation time. Before/after observations preserve exact selected snapshots; a
changed ID is an observed change, not reconstructed history. Metric queries pass
public `source`, time and aggregation controls through unchanged. Empty samples,
missing labels, snapshot failures and gaps remain visible. Status enum values and
API-provided `value_label` are preserved without invented integer-to-text maps.

```
python3 -m evaluation.consumers.api_only --endpoint http://127.0.0.1:18081 \
  --before 2026-10-31T12:00:00Z --after 2026-10-31T12:10:00Z \
  --query '{"metric_name":"k8s.pod.phase","source":"kubeletstats"}'
python3 -m unittest evaluation.tests.test_gke_references evaluation.tests.test_gke_consumer
```
