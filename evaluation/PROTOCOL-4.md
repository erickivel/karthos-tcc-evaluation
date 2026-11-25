# Protocol 4: repair validation and evidence gates

Status: readiness qualified and the full32window campaign started on2026-10-22 as `results/repairs-v4-20261022-a`. Live progress is in that run's run-report.json. Readiness does not imply all pilot outcomes have passed. Historical studies retain their original IDs, checksums, thresholds and results.

The approved revision allows **clean reruns for readiness while retaining the original cause as unknown**, superseding the original requirement to prove the historical initiating cause. The new prospective gate requires three distinct60second Astronomy mesh k6 attempts with verified complete generator exports, matching scored identities, valid source/oracle/capture evidence, and no unexpected503s or application outcomes. It does not require numerical disagreements to disappear.

**Earlier gate before mitigation: not passed.** Attempt1 passed; attempt2 reproduced503 with the same Envoy `UC` connection-termination mechanism. A separate4.2second kubelet sampling gap during drain made that attempt's structured capture incomplete. Attempt3 was not started after the required evidence gate failed. See the [new failure](../results/repairs-v4-preparation-20261021/astronomy503/RECURRENCE-20261022.json) and [user decision](../results/repairs-v4-preparation-20261021/astronomy503/READINESS-DECISION-20261022.json). The earlier [investigation](../results/repairs-v4-preparation-20261021/astronomy503/HISTORICAL-503-INVESTIGATION.md) and strict [historical gate](../results/repairs-v4-preparation-20261021/astronomy503/causal-gate.json) remain unchanged as prior records.

## Commands and readiness

Run from the evaluation repository root. The following commands inspect the plan and existing results without deploying workloads:

```bash
python3 -m evaluation.repair_pilot --show-plan
python3 -m evaluation.repair_pilot --verify results/<existing-protocol4-run>
```

`--show-plan` prints the exact 32-window schedule and returns before readiness or live work. `--verify` recomputes per-window verification and writes `verification.json`, `summary.json`, `SUMMARY.md` and checksums for the supplied existing run. It is offline with respect to the cluster but does update those result artifacts.

The live entrypoint requires an evidence-backed readiness file before preflight, image build, deployment or traffic:

```bash
KUBECONFIG=<explicit-cluster-config> python3 -m evaluation.repair_pilot \
  --readiness <readiness.json> --run-id <new-run-id> \
  --deadline-at <absolute-RFC3339-deadline>
```

This command is documented for use only after all readiness requirements are met. `--smoke-only` requests the eight smoke windows; it does not bypass readiness. `--replace-existing` is an explicit input to the existing ownership-aware preflight/deployment flow and does not authorize deleting unrelated resources. A run ID must be one directory name, and the output directory must be new.

`readiness_issues()` requires all of the following:

| Gate | Required record |
|---|---|
| Product checks | `product_ci.state=pass` with nonempty `evidence` entries containing `path` and `sha256` |
| Harness checks | `harness_tests.state=pass` with hashed evidence |
| Focused live repairs | `live_repairs.state=pass` with hashed evidence |
| Astronomy503 | Either the legacy resolved-cause proof, or `state=recurrence_validated`, `historical_cause=unknown`, `readiness_policy=user-approved-recurrence-v1`, hashed user `decision`, and three distinct hashed attempt records |
| Frozen inputs | `product_source_hash` matches the current product repository fingerprint; `harness_fingerprint` matches the current resume fingerprint |

Evidence paths may be absolute or relative to the readiness file's directory. Missing or changed files block launch. The checker validates structure, hashes and declared states; it cannot establish that an arbitrary prose cause is true. For the recurrence alternative, every attempt supplies hashed `manifest`, `verification`, `ledger` and `artifact_manifest` entries from one distinct exported window. The checker binds the score to the exact manifest, revalidates the complete raw generator export, reconciles its REQUEST records and native counts with the supplied ledger, and checks all120planned journeys. A sanitized ledger or substituted clean score cannot certify a failed attempt. This establishes prospective readiness only. The archived historical blocked gate is not a ready-to-run readiness file.

## Exact schedule: 32 windows

All matched windows use the shared `checkout-heavy` recipe. Rate is **journeys/second**, not HTTP requests/second. The baseline is 2 journeys/s; native pacing remains owned by the pinned upstream Locust workload and has no assigned matched rate. This table follows `schedule()` and `cells_for_repeat()` exactly; phases and windows run serially.

| Window | Phase | Repeat | Demo | Source | Generator / mode | Duration | Rate |
|---:|---|---:|---|---|---|---:|---:|
|1|smoke|1|Boutique|trace|k6 matched|60s|2|
|2|smoke|1|Boutique|trace|Locust matched|60s|2|
|3|smoke|1|Astronomy|trace|Locust matched|60s|2|
|4|smoke|1|Astronomy|trace|k6 matched|60s|2|
|5|smoke|1|Boutique|mesh|k6 matched|60s|2|
|6|smoke|1|Boutique|mesh|Locust matched|60s|2|
|7|smoke|1|Astronomy|mesh|Locust matched|60s|2|
|8|smoke|1|Astronomy|mesh|k6 matched|60s|2|
|9|measured|1|Boutique|trace|k6 matched|300s|2|
|10|measured|1|Boutique|trace|Locust matched|300s|2|
|11|measured|1|Boutique|trace|Locust native|180s|—|
|12|measured|1|Astronomy|trace|Locust matched|300s|2|
|13|measured|1|Astronomy|trace|k6 matched|300s|2|
|14|measured|1|Astronomy|trace|Locust native|180s|—|
|15|measured|1|Boutique|mesh|k6 matched|300s|2|
|16|measured|1|Boutique|mesh|Locust matched|300s|2|
|17|measured|1|Boutique|mesh|Locust native|180s|—|
|18|measured|1|Astronomy|mesh|Locust matched|300s|2|
|19|measured|1|Astronomy|mesh|k6 matched|300s|2|
|20|measured|1|Astronomy|mesh|Locust native|180s|—|
|21|measured|2|Astronomy|mesh|k6 matched|300s|2|
|22|measured|2|Astronomy|mesh|Locust matched|300s|2|
|23|measured|2|Boutique|mesh|Locust matched|300s|2|
|24|measured|2|Boutique|mesh|k6 matched|300s|2|
|25|measured|2|Astronomy|trace|k6 matched|300s|2|
|26|measured|2|Astronomy|trace|Locust matched|300s|2|
|27|measured|2|Boutique|trace|Locust matched|300s|2|
|28|measured|2|Boutique|trace|k6 matched|300s|2|
|29|diagnostics|1|Boutique|trace|k6 matched|300s|4|
|30|diagnostics|1|Boutique|trace|Locust matched|300s|4|
|31|diagnostics|1|Astronomy|trace|Locust matched|300s|4|
|32|diagnostics|1|Astronomy|trace|k6 matched|300s|4|

There are 8 smoke, 16 matched baseline, 4 native descriptive and 4 higher-rate diagnostic windows. Generator order reverses in repeat 2, along with cell order. Native runs occur only in measured repeat 1; higher-rate diagnostics do not replace a baseline repeat. k6 has an open arrival process; matched Locust uses closed users with throughput pacing. Their delivered rate and burst shape can differ, so generator identity and load shape remain confounded. Native workloads are descriptive, with upstream tasks/pacing retained; they do not establish matched business-outcome parity.

## What each verdict means

Each window retains distinct `execution`, `evidence`, `numerical_agreement` and `application_outcomes` records:

- **Execution:** closed, verified artifact exports establish completion. A workload can finish and export evidence while reporting application failures. Interrupted execution or incomplete artifacts remain incomplete.
- **Evidence:** complete generator accounting, source evidence, structured capture, and successful guarded restoration are required. Missing capture, rotation, restart, ambiguous input identity or unavailable sources cannot become manufactured zeroes.
- **Numerical agreement:** compare only eligible populations and boundaries under frozen thresholds. Count mismatch makes duration population equivalence unproved; duration values remain available but the affected duration comparisons become incomplete.
- **Application outcomes:** matched requests use declared status and body predicates. Expected failure branches remain valid workload outcomes; unexpected 503/500 responses remain failures. Native HTTP outcomes remain unclassified/descriptive until route-specific business rules exist.

A numerical pass does not prove application success or complete source accounting. Valid failure evidence is retained. Matched windows with invalid required oracle/source/capture evidence stop campaign progression; numerical disagreement is recorded separately. `--verify` summarizes available windows and reports whether 32 were exported; counts alone do not supply missing evidence or prove every result passed.

## Measurement bounds and full capture

The scheduled window is fixed at workload start plus the declared duration. The manifest separately retains actual workload end, completion-tail duration, in-flight completions and requests started after scheduled end. Telemetry drain and artifact recovery do not extend the scheduled arrival window. Default configuration records 60s capture preroll and 90s telemetry drain; the locked run config remains authoritative.

Protocol 4 installs temporary, window-owned capture around the real Karthos collector path:

- Agent and gateway metric pipelines add bounded JSON file exporters; gateway raw trace ingress is captured through a separate pipeline before pairing/derivation.
- A local agent proxy records the exact kubelet receiver response, collector pod UID, endpoint and acquisition times. The original upstream kubelet endpoint is retained. This distinguishes conversion/preservation from agreement with an independent underlying measurement.
- Independent kubelet observations are matched by node, pod UID, container and underlying measurement timestamp. Ambiguous/missing matches are incomplete. Cache age, acquisition alignment and stored freshness remain visible.
- Kubelet, mesh and collector-self-metric loops run independently at nominal 1s, 2s and 5s respectively. Actual request/receive times and gaps are retained; nominal cadence is not a claim of achieved coverage. Mesh sampling is explicitly not applicable in trace cells.
- Inventory, logs, raw files, source status, storage exports, API responses, generated configuration, manifests and checksums remain part of the audit chain. Empty logs cannot substitute for raw trace coverage.

Instrumentation creates reader/proxy containers and rolls collector pods. Setup, capture resources and cleanup timing are reported separately from the workload. Full capture itself adds CPU, memory, disk and API traffic; these runs cannot establish production overhead without a separate overhead study. Source counters are not assumed equivalent to application latency spans.

## Locust final reports and request accounting

`workloads/locust_reports.py` writes final stats/failures/exceptions CSV snapshots through Locust's `locust.stats.StatsCSV` API at the quit event, after runner shutdown. Each file is flushed and atomically replaced so periodic CSV writers cannot overwrite the final snapshot through an old file handle. Reports and raw ledger files remain separately hashed native artifacts.

The native request adapter observes upstream request events and adds bounded correlation headers/context; it does not replace upstream tasks, payloads or pacing. The attempt ledger and terminal ledger must reconcile exactly. **Shutdown-aborted attempts and actual Locust request events are distinct accounting categories:** an adapter terminal record without a Locust event must not inflate the native CSV request count or be silently dropped. `native_reported` marks whether a Locust request event was observed; the artifact verifier compares the CSV aggregate with event-reported terminal rows, not every terminal row. Duplicate/missing attempt terminals still invalidate evidence.

Pinned-image integration probes passed with Locust 2.43.0 (Boutique, amd64) and 2.44.4 (Astronomy, arm64). The retained [final probe evidence](../results/repairs-v4-preparation-20261021/native-image-probes-final-v4/verification.json) covers redirects, intentional HTTP failures, transport failures, and shutdown-aborted attempts. Each version retained two aborted attempts separately; CSV request and failure totals matched native event rows. Earlier failed probes remain alongside it. Astronomy’s version command also emitted a Python/gevent finalization warning, retained verbatim; it did not prevent the workload and complete export. HTML/CSV presence alone is insufficient. Native outcome records remain descriptive even when request accounting is complete.

## Limits, recovery and guarded cleanup

The absolute study deadline is checked before workload launch; per-window budget includes capture setup, workload, export and drain. Protocol 4 reserves 1200s for instrumentation in its budget calculation. Default study budget is 8 hours unless an explicit deadline is supplied. An idle export-recovery allowance of 1800s may extend beyond the study deadline; it permits recovery only, never new traffic. Native process timing excludes artifact hold.

Each collector capture uses a 512Mi emptyDir. File rotation is bounded at 64Mi per file with two backups, but any observed rotation invalidates that window conservatively. Archive extraction rejects unsafe members and totals above 512Mi; export retries are bounded at three attempts per component, with the recorded combined local bound 6 × 512Mi. Partial exports and their attempt statuses are preserved. Disk bounds and nominal sampling periods are not completeness proofs.

Before the first collector mutation, durable snapshots and a narrowly scoped in-cluster watchdog are installed. Server-side dry runs validate the planned patches. Installation/restoration use resource UID and content tests, a unique capture owner and the evaluation ownership marker. Cleanup restores saved config before saved workload template and verifies restoration. If UID, owner, expected content or marker changed, restoration refuses to overwrite it, reports invalid capture and leaves evidence/watchdog resources for inspection. The watchdog uses the saved deadline plus recovery allowance; cluster/API failure can still prevent cleanup, so retained terminal status and live restoration checks are required.

The retained application environment is available for inspection after a run. No broad cluster deletion, external Istio reset, historical-score rewriting or threshold relaxation is part of this protocol. A supported503readiness path remains required even if collector repairs and local checks succeed; the user-approved prospective alternative never labels the historical cause resolved.

## Historical reanalysis

Use `python3 -m evaluation.reanalyse --protocol 4 SOURCE NEW_OUTPUT` to analyse preserved evidence under the new verifier without changing the source directory. Output must be new and outside SOURCE. The analysis records hashes of every original file and verifier files, checks immutability before publication, and explicitly lists missing exact receiver/structured evidence. Do not use the in-place `repair_pilot --verify` command on historical studies.

## Preparation findings incorporated before the study

- Capture restarts collectors. Protocol4 therefore records a20second,1journey/s k6 priming control **after instrumentation**, outside the measurement interval, followed by the unchanged preroll and quiet-counter diagnostic. Priming artifacts remain under each window's `warmup/`; it is not one of the32studywindows and never shifts their boundaries. Its time consumes the study budget.
- Source relay selfmetrics are required, not inferred from successful forwarding. Protocol4 explicitly enables a pod-local reader on127.0.0.1:8888 in the two evaluation demo presets. Earlier Astronomy values disabled this endpoint. See the archived [version-specific contract](../results/repairs-v4-preparation-20261021/upstream-selfmetrics/CONTRACT.md). Missing inventory, config, forward or startupmetrics blocks traffic.
- CPU/memory query evaluation timestamps use VM integer milliseconds. API timestamp serialization can have fractional floating-point tails; parsing it as a lower-precision datetime previously selected the preceding stored sample. The verifier records the original timestamp and normalization delta, selects by integer timestamp and complete identity, and keeps raw OTLP nanoseconds and kubelet measurement timestamps separate. No metric tolerance was widened. See [offline precision proof](../results/repairs-v4-preparation-20261021/offline-probe-3-vm-ms-20261022/resource-ms-comparison.json).
- The protocol4 campaign freezes a256MiB combined stdout/stderr cap per stream; standalone capture defaults to64MiB unless explicitly configured. The effective cap is recorded in `structured/status.json`. Overflow is an explicit capture failure; bytes are preserved and neither truncation nor rotation is used to hide loss. Job cleanup is foreground so watchdogchildren disappear before scripts/RBAC are removed.

Manual preparation attempts must also use unique study/window IDs. Reusing IDs against a retained stateful demo can accumulate cart quantities in abandoned or expired journeys. The main pilot refuses an existing run directory and includes study, cell, generator and journey identity in its request namespaces. Preparation artifacts record execution, business outcomes and numerical agreement separately.

## Preparation evidence, 2026-10-22

The retained Astronomy trace preparation control with matched Locust and fresh study identities completed with valid oracle, source and capture evidence. Its execution, numerical agreement and declared application outcomes passed. This60second preparation is not a formal campaign window and does not establish five-minute or mesh readiness. See [control5](../results/repairs-v4-preparation-20261021/live-capture-probe-5/preparation-result.json).

A read-only cart check after that control found quantity3 for three reused manual preparation IDs and quantity1 for the corresponding fresh IDs. Earlier quantity failures remain in their original bundles; fresh IDs prevent this preparation-state contamination. See [observations](../results/repairs-v4-preparation-20261021/cart-identity-check/observations.json).

Resource overhead reporting separates application containers, source collectors, Karthos, evaluation capture, generators, observers and mesh sidecars. These are measured usages, not estimates of incremental causal instrumentation cost.

## Raw gRPC compatibility and focused packet diagnostic

The verifier recognizes both legacy numeric `rpc.grpc.status_code` and schema1.43 symbolic `rpc.response.status_code` when the protocol identifies gRPC. Original status attributes are retained; conflicting, malformed or non-gRPC outcomes stay unknown. This does not change connector-native span-error semantics. See the [deployed-source contract](../results/repairs-v4-preparation-20261021/RAW-GRPC-OUTCOME-CONTRACT.md).

`python3 -m evaluation.diagnostics.astronomy_packet_probe --run-id <new-id>` is a separate, explicitly targeted Astronomy mesh diagnostic. It requires the owned namespace and actual generated mesh scrape jobs. It runs180seconds of matched k6 at2journeys/s,60seconds preroll,90seconds drain, request artifacts and selected source/application logs. It makes no full resource-accuracy claim.

Two temporary packet sidecars use a digest-pinned tcpdump image, NET_RAW only, no host namespaces or host mounts,32Mi perpod,50000packets,512byte snapshots and a600second limit. An admitted-template snapshot and independently ready watchdog precede application changes. Closed exports have three separate bounded attempts; cap exhaustion, kernel drops, missing bounds or early completion remain incomplete. Guarded restoration returns both application templates and waits for readiness. This diagnostic's rollout and capture overhead must not be treated as production behavior or a formal pilot result.

### Idle-boundary diagnostic

Use the same explicit KUBECONFIG and KARTHOS_REPO as the preceding controls:

```sh
python3 -m evaluation.diagnostics.astronomy_packet_probe \
  --mode idle-boundary --run-id <new-id>
```

This is a closed one-user k6 diagnostic:21pairs/42GETrequests, seven pauses from5.995to6.005seconds, two persistent pairs and one fresh-downstream-connection control per pause. It targets the captured frontend-proxy PodIP directly on8080, bypassing the nginx observer; this path change is recorded. A new downstream connection does not prove a new upstream connection: use packets to determine that. Actual request/response times, measured pauses, traceparents and request IDs remain in the ledger. Packet coverage ends at the last actual request completion;300seconds is a maximum execution budget, not a scored workload window. Missing dropped_iterations is explicitly not applicable to this closed model. The existing matched warmup ConfigMap is read without rewriting it; the probe uses a separate owned ConfigMap and guarded cleanup.

The exact pinned k6 image completed a local HTTP/1.1 fixture with42successful requests,14reused pairs and7fresh pairs; native totals and closed artifacts reconciled. The temporary fixture controller had a reporting-key error after validation, retained separately in [the runtime check](../results/repairs-v4-preparation-20261021/idle-boundary-local-validation/result.json). This is not evidence that a cloud or cluster timeout failure is resolved.

Decode capture files using the pinned Linux decoder:

```sh
python3 -m evaluation.diagnostics.analyse_packet_probe SOURCE NEW_ANALYSIS_DIRECTORY
```

Original packet files remain unchanged. The analyser checks exported checksums and nanosecond pcap timestamps against the binary header. Linux Tshark emits boolean TCP flags as True/False, which must be interpreted explicitly. The first corrected analysis handles both those flags and numeric fixture flags; the preceding analysis with empty close lists is superseded. Host macOS tcpdump rendered nanosecond fractions incorrectly and is not a timing reference.

The first clean matched packet probe completed938requests without unexpected outcomes and restored both templates. Its90observed application-initiated closes had idle times5.99943–6.00366seconds (median6.00033). These normal closes support testing the idle-timeout hypothesis; they do not establish the initiating cause of either503. See [exact Linux analysis](../results/repairs-v4-preparation-20261021/packet-analysis-linux-2/analysis.json).

### Prospective failure reproduced with packet evidence

The idle-boundary probe completed42requests and reproduced3HTTP503s, with valid generator and packet evidence and both deployment templates restored. Each failing request ID appears in a captured application-hop GET. The application FIN precedes that GET on the same connection by0.112–0.395ms; the application then sends RST and the frontend-proxy returns503. This supports a prospective idle-connection reuse race in the demo/proxy path; it is not proof of Karthos metric loss. A fresh downstream client control also failed. See [causal report](../results/repairs-v4-preparation-20261021/idle-boundary-causal-analysis-a/REPORT.md) and its exact frame/hash references.

The original historical cause remains unknown. At this earlier checkpoint, a proxy timeout correction had not yet been validated and the separate checkout-rerun gate had not passed. These diagnostic outcomes are preserved as failures; none are removed by changing a metric tolerance or adding retries.

### Correction integrated and checkout readiness in progress

The unchanged42request idle probe with the explicit4second upstream policy produced0HTTP503s (previously3). Both generator/packet evidence passed and application templates were restored. Application-hop connections now closed first from the proxy side at a median4.00048seconds; application-hop reset frames fell from3to0. Capture-wide close counts are separate from the42request ledger population. See [bounded comparison](../results/repairs-v4-preparation-20261021/idle-timeout-comparison-a-b/REPORT.md). This does not assign a cause to the historical incident or estimate a production failure rate.

Protocol4 embeds the hash-checked Astronomy3.1.0 source template in a chart-managed ConfigMap via a subPath mount. The exact image, entrypoint and environment substitution remain intact. Earlier protocol presets are unchanged. Existing HTTP policy fields are rejected for manual review. Run fingerprints now include YAML. The full277test suite passed, and the actual pinned chart rendered the same candidate template hash as the live diagnostic.

An ad hoc Helm4 upgrade conflicted with evaluator-managed Service fields; client-side upgrade then reset those observer ports. Exact prior port records were restored with UID/current-value guards before the diagnostic measurement. Normal cell setup installs observers after Helm, so the correction must be applied in demo values during initial installation, before observers are installed. Do not blindly upgrade an instrumented cell and assume observer routing survives. All failed upgrade logs remain archived.

The three distinct60second checkout windows under the corrected configuration subsequently passed. They include full source, resource, collector/storage/API and generator evidence; the idle probe did not replace them. The final strict proof is readiness-v4-final-20261022.json under the preparation directory; the full32window study has now started.

## Qualified readiness and launched study

All three corrected checkout attempts passed execution/oracle, source, complete capture, application outcomes and numerical agreement. All collector instrumentation was restored. The historical initiating cause remains unknown, as agreed.

|Attempt|Full raw requests|Fixed-window scored requests|Actual journeys|CPU/memory checks|Result|
|---:|---:|---:|---:|---:|---|
|1|314|312|121|250|pass|
|2|312|312|120|250|pass|
|3|314|312|121|250|pass|

The readiness checker initially compared the full execution ledger with fixed-window scored totals. Two successful requests in attempts1and3 began just after the boundary. This was an acceptance-bookkeeping defect: the score correctly filtered by request start. After controls completed with an unchanged executed harness, the admission checker was corrected and tested. It still requires byte-exact raw-ledger/export equality, globally clean outcomes including tails, every planned journey beginning inside the window, and start/end anchored to the generator marker and original60seconds. It does not shift windows, discard failures or widen tolerances. The full283test suite passed; the readiness proof records the validation-only code delta from the executed controls.

The current product matches all530source hashes of the tested kind build. The qualified evidence is [readiness-v4-final-20261022.json](../results/repairs-v4-preparation-20261021/readiness-v4-final-20261022.json). The pilot began at16:37SãoPaulo time onOctober22and has an absolute00:37October23deadline. The launch monitor retains a local8GiB export reserve and asks the controller to stop normally if it is reached. This is separate from numerical acceptance. See [launch evidence](../results/repairs-v4-preparation-20261021/formal-pilot-launch-20261022-a/launch.json).

## Repaired native-client segment — October23UTC

The first formal segment stopped at Boutique native Locust:205requests completed in the upstream report but no request-ledger events were emitted. The upstream workload uses FastHttpUser; the adapter intercepted HttpSession only. Closed export files arrived and their checksums were preserved. This is an evaluation instrumentation defect.

The correction wraps both supported clients through one shared event/window logger. Regression evidence and actual-upstream old/fixed probes are in `results/repairs-v4-preparation-20261021/native-fasthttp-fix-20261023`. The full harness suite passes284tests. Product code, workload pacing, windows, and tolerances remain unchanged.

The original `repairs-v4-20261022-a` results stay immutable. Continuation `repairs-v4-20261023-b` records a distinct harness revision, inherits eight smoke windows and two baseline windows with valid evidence, and labels the native retry separately. Numerical incompleteness in the retained five-minute k6 result remains visible. Both segments share the original deadline2026-10-23T03:37:17.698436Z (00:37SãoPaulo). The combined matrix can remain incomplete at that deadline.

## Native correlation repair and approved continuation — October23

Verifier4.1.0 separates requested trace context from response.request.headers after upstream instrumentation injects the final context. The preserved Astronomy native window has208frontend requests whose wire trace IDs all appear in raw capture, while ledger guesses do not match. Its14directPOST/prompt calls bypass the frontend by design. Historical results remain unchanged.

Astronomy cells with native windows now include an independent application-agent observer. Native requests declare a reviewed destination boundary; unknown destinations and unavailable/mismatched correlation evidence remain incomplete. A numerical status disagreement with both records available remains a measured discrepancy. The original upstream native task bodies, payloads and pacing remain enabled. Extra observer overhead is recorded separately.

The correction passed295harness tests, active-upstream-instrumentation image checks136/136matching received trace IDs, and fast-client redirect/failure checks in both pinned images. Evidence and the explicit user approval are in `results/native-context-repair-20261023`.

A new budget of up to 8 hours was approved for `repairs-v4-20261023-c`, covering19remaining windows: the failed Astronomy native retry and18unexecuted windows. Thirteen previous evidence-valid windows remain inherited under their original verifier versions; two have incomplete duration comparisons. Numerical tolerances, matched load scripts, source configuration and product code remain unchanged. The original expired deadline is retained in earlier segments; the new segment records its own approved deadline and preserves30-minute idle artifact recovery.


## Independent sampler storage repair (verifier 4.1.1)

The October 23 Boutique mesh k6 attempt in `repairs-v4-20261023-c` remains
incomplete: its uncompressed mesh sample file reached the hardcoded 256 MiB
limit before workload completion. Successful requests do not repair its missing
source coverage. The failed attempt and historical checksums stay unchanged.

New protocol4 windows store the independent mesh, kubelet and collector samples
as `.ndjson.gz`. Each record is a closed gzip member (level 1); the full original
metric text, labels and acquisition timestamps are preserved. Cadences remain
2s mesh / 1s kubelet / 5s collector self-metrics. This is a storage-format repair,
not a source filter, change in query semantics, or tolerance adjustment.

`capture_sample_max_bytes` defaults to **256 MiB per stream on disk**.
`capture_sample_max_uncompressed_bytes` defaults to **8 GiB per stream**; this
additional logical safety bound covers the projected sampling envelope including
the possible 30-minute idle export hold. Supported overrides are positive
integers, at most 2 GiB stored / 8 GiB logical. Exhaustion stops that sampler and
invalidates the window; there is no rotation, silent discard or automatic resize.
Collector exporter and application log bounds remain separate.

Before workload launch, capture takes one full sampling tick per stream and
records a capacity projection with a factor of two for growth. The planned
capture envelope includes workload duration, 180s generator startup/completion,
preroll, drain, artifact export, two bounded 90s counter diagnostics and the
configured idle recovery allowance. Free disk must cover the combined projected
sampler files plus a 512 MiB reserve; other capture/export budgets remain
separate. Estimates do not guarantee future volume: hard runtime limits and
sampling-gap checks remain authoritative. Insufficient capacity blocks the
window before new load starts and follows normal instrumentation restoration.

`structured/sampling-status.json` is atomically updated during capture and on
sampler failure. It records records/bytes, first/last acquisition timestamps,
capacity projections and termination time. `structured/status.json` includes
final sampling status and verifies gzip integrity before accepting capture.
Missing, empty, truncated or corrupt compressed files do not become successful
empty evidence. Historical plain NDJSON is still supported; simultaneous plain
and compressed candidates are rejected as ambiguous. New result directories
must pin the changed harness/config; old attempts are never recompressed in place.
