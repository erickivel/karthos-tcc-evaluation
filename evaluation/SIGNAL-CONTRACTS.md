# Pilot v2 signal and scoring contracts

The deployed image IDs and generated configuration in each cell override any assumption below. A changed contract requires a new study ID. Frozen tolerances are pilot bounds, not production accuracy claims.

## Requests and outcomes

k6 1.8.2 starts open-model iterations at the declared rate; Locust uses ten closed users with `constant_throughput(rate/10)`. Both use one versioned recipe and export attempts before the call and terminal records after it. An iteration is a journey and can contain several HTTP requests. Locust's initial population can burst and slow responses can reduce its achieved rate; report this rather than modifying the schedule after scoring. Redirects are explicitly disabled and Boutique cart-add HTTP 302 is an expected result. The native Locust scripts retain their original journeys and are descriptive, not substitutes for the matched ledger.

The legacy recipe retains the original logical route mix and 29 checkouts in 360 iterations. Version 2 corrects cookies, query parameters, redirect accounting, and outcome validation. It is not a byte-identical replay of the first study. The five-minute recipe has five successful checkouts and one expired checkout per ten iterations. Boutique returns HTTP 500 containing the expired-payment failure; Astronomy returns HTTP 422 with `PAYMENT_FAILED`. Keep actual HTTP failures distinct from unexpected outcomes and from gRPC failures on the selected edge.

References: [k6 arrival executors](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate/), [k6 response status contract](https://grafana.com/docs/k6/latest/javascript-api/k6-http/set-response-callback/), [Locust pacing](https://docs.locust.io/en/stable/api.html#locust.constant_throughput), and pinned application source links in [workload contracts](workloads/README.md).

## Independent boundaries

Evaluation-owned nginx 1.28.0 observers are added as extra containers in the frontend and checkout Pods. Only the Services' target ports change; the application listens on its original port. Observers disable proxy retries and emit JSON access records. gRPC status is read from upstream trailers, falling back to response headers. Their overhead is reported separately. The frontend can be joined to generator request IDs; the checkout call usually lacks that propagated header and is compared as a time-bounded aggregate. Observer duration includes local proxy/transport time at millisecond resolution; it is never described as exact application execution time.

References: [nginx gRPC module](https://nginx.org/en/docs/http/ngx_http_grpc_module.html), [nginx log format and request time](https://nginx.org/en/docs/http/ngx_http_log_module.html).

## Raw traffic source

Trace cells retain full detailed debug-exporter output from the demo relay/collector through the observation and drain periods. `sampling_thereafter: 1` and `use_internal_logger: false` explicitly avoid debug-output sampling and internal JSON formatting. Parse server checkout spans, pair them with frontend client spans using trace and parent IDs, and report unpaired spans. Connector metrics represent paired client/server calls rather than all frontend HTTP requests. Inspect and retain actual pairing TTL, flush interval, cache limits, histogram buckets, and temporality from generated Karthos values. The existing generated servicegraph flush is 15s and pairing TTL 30s; these must be reverified per run.

Mesh cells use destination-reporter `istio_requests_total` and duration histogram sum/count for the frontend-to-checkout edge. Do not sum source and destination reporters. Protocol names and `appProtocol: grpc` are explicit on checkout Services. Preserve all source dimensions and report counter resets. Missing error series remains missing unless a zero observation is actually available.

References: [servicegraph connector v0.155.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.155.0/connector/servicegraphconnector/README.md), [debug exporter v0.155.0](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.155.0/exporter/debugexporter/README.md), [Istio protocol selection](https://istio.io/latest/docs/ops/configuration/traffic-management/protocol-selection/). Astronomy's pinned chart includes its own collector version, separately recorded from Karthos and the Boutique relay; an unsupported debug output shape invalidates that oracle.

## CPU, memory, and storage stages

The exact v0.155.0 and v0.156.0 kubelet receiver source checked for this pilot reads CPU `UsageNanoCores / 1e9` as cores; Karthos converts cores to millicores. Thus the reference is `usageNanoCores / 1e6`. Memory reference is `usageBytes`, not `workingSetBytes`. Preserve each kubelet `cpu.time` and `memory.time`, fetch time, and cache age. Reject readings outside the frozen alignment bound; a recent API fetch alone does not prove a fresh reading. Export every five seconds; retain missing fields and restarts.

Compare the reference to exact stored VictoriaMetrics export samples, then stored samples to API evaluation points with the actual lag shown. API `latest` range output is evaluated on a query grid, not necessarily stamped at the original kubelet observation. Score every application container from Kubernetes inventory even when its canonical graph entity is missing. Classify observer/Istio/generator/collector/backend/store overhead separately.

References: [kubelet metrics v0.155.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.155.0/receiver/kubeletstatsreceiver/documentation.md), [v0.156.0 CPU source](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/receiver/kubeletstatsreceiver/internal/kubelet/cpu.go), [collector self telemetry](https://opentelemetry.io/docs/collector/internal-telemetry/). Collector metrics are scraped through port-forward because their default endpoint binds to loopback. Export raw counters and queue/drop indicators rather than assuming a version-independent counter name or a zero for a failed scrape.

## Windows, tolerances, and interpretation

Generator artifact protocol1 requires verified raw file hashes and completion semantics before ACK or cleanup. Download transport success alone does not establish complete evidence. A workload exit code is separate from the held container/Job status, and native workload timing excludes the artifact recovery period. Source/app/Karthos logs still use timestamp-cursor polling; generator truth and native reports use direct file export. Scoring revalidates raw exported files on every invocation; no missing client records are reconstructed from frontend observations.

Generator markers record actual start/end and scheduled iteration budget. The active observation includes graceful completions. Raw VM export includes 60s before and 90s after that window to retain counter brackets. Counter deltas include resets and report boundary slack; they are not extrapolated. Mesh boundary allowance is 15s, VM allowance 45s (30s collection plus 15s flush). Gauge reference alignment is 10s and freshness allowance 60s. API count/duration query parameters and original pages are retained. Duration partition means are weighted by observed histogram counts, not averaged equally; compare independent proxy→API, source→VM, VM→API, and source→API views.

Independent log and kubelet capture starts 60s before the generator, covering the permitted cached history. Log snapshots use whole-second `--since-time` cursors because Kubernetes `metav1.Time` query serialization uses RFC3339 without fractional seconds ([upstream implementation](https://github.com/kubernetes/apimachinery/blob/v0.35.2/pkg/apis/meta/v1/time.go)). Exact nanosecond timestamps and payload multiplicities remain in overlap accounting; repeated legitimate lines must survive. Capture errors invalidate evidence. The kubectl client version is retained in preflight. The earlier followed-stream approach exhausted the local k3s server's inotify-instance allowance during Astronomy smoke; polling avoids changing host limits.

Fixed provisional bounds: count `max(2 requests, 15%)`; duration `max(5ms, 20%)`; CPU `max(10 millicores, 20%)`; memory `max(1 MiB, 20%)`. The four-cell smoke qualifies complete generator/source capture before these bounds are frozen and retains declared-versus-observed route outcomes. It does not fit bounds to observed errors or require every numerical/outcome comparison to pass. Application failures with a usable oracle remain failed quality observations and continue through the matrix; inconsistent ledgers, interrupted required streams, wrong selected source, and deadline failures stop and preserve the attempt. The final report distinguishes execution completion, metric agreement, and absent evidence.

## Protocol 3 outcome and comparison contract

Observed HTTP/gRPC dimensions are the authoritative request outcome evidence.
For the mesh error view, the declared policy is HTTP 500–599 or a present nonzero
gRPC status, with gRPC status taking precedence for gRPC traffic. Missing gRPC
status is unknown. Trace connector failed counters retain their native meaning
and must carry explicit connector-native derivation provenance; equivalence with
mesh classification is not assumed. Intentional injected failures remain measured
failures; workload expectation checks are separate from error-count accuracy.

The protocol keeps source accounting, storage/API parity and workload agreement
separate. Direct storage parity queries use the exact production query compiler,
source selector and evaluation timestamps, with VM caching disabled as in the
production storage adapter. Full status-partitioned series must
match before reset-aware increase/rate calculation or duration aggregation.

Quiet diagnostics inspect stored checkout counters before and after the load,
without extending, shifting or substituting the original scoring window. Two
unchanged observations must have advanced producer timestamps and be separated
by the measured collection interval and satisfy the frozen freshness bound at
both observations. Absence, stale samples and a 90-second
failure to stabilize remain explicit diagnostic limitations.

Raw histogram sum/count samples, kubelet producer timestamps, container lifecycle
inventory, source counters, generator ledgers and collector logs remain available
to investigate differences. Finished ordinary init containers are not expected to
have steady-state resource observations; running sidecars and restartable init
sidecars have separate overhead expectations.
