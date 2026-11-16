# Official evidence recovery

The November 15 recovery uses the original `official-thesis-v2-20261115t0020z` archives and product revision `4c2861b`. It changes the verifier, not the original observations or thresholds. Install `requirements-analysis.txt` in an isolated Python environment before offline analysis.

```sh
python -m evaluation.gke.accuracy_reanalysis ARCHIVE NEW_ACCURACY_DIRECTORY --sha256 SEALED_ARCHIVE_SHA256
python -m evaluation.gke.claim_recovery ARCHIVE NEW_CLAIM_DIRECTORY --sha256 SEALED_ARCHIVE_SHA256 --accuracy NEW_ACCURACY_DIRECTORY/summary.json
```

Both destinations must be new. Obtain the digest from the sealed run/checkpoint, not from an untrusted archive itself. The streaming reader validates every original report byte while omitting duplicate matched-reference bodies and availability payloads from its in-memory analysis projection. It does not remove those records from the original archive. Exact conversion and independent reference checks are recomputed from retained inputs. Raw measurement timestamps remain exact; only API query evaluation timestamps use the VictoriaMetrics millisecond identity contract.

Graph producers are explicit: connector metrics produce `traces.servicegraph` observations; direct spans can produce `traces.otlp`. A corrected source-filtered historical query cannot be retroactively claimed as executed. Positive snapshot observations recovered offline are labeled separately, and do not establish complete historical caller-set membership.

Application reproducibility excludes only the generator Job and reference DaemonSet attested in their retained Kubernetes documents, plus their `contains` descendants. It never removes application nodes by a broad name-prefix pattern. Interval graph comparisons use retained history and preserve a separate current-snapshot call count.

## Next deployment safeguards

Astronomy `currency` is assigned to application A; payment and checkout remain on B. Requests and limits are unchanged. The pre-Agent check records current assigned Pod CPU reservations, including ordinary init, restartable init sidecars and Pod overhead, then adds the missing Agent reservation from the evaluation resource policy. It rejects a demonstrated shortfall before the Helm rollout wait. This is a necessary admission check, not a full scheduler simulation or a guarantee of future rollout capacity. Affinity, taints, pending Pods, other planned components and transient rollout overlap must still be verified by the deployment/placement gates before traffic. Historical incomplete inventories do not prove the exact old shortfall.

CPU accounting follows Kubernetes [sidecar scheduling semantics](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/) and [KEP 753 resource calculation](https://github.com/kubernetes/enhancements/blob/master/keps/sig-node/753-sidecar-containers/README.md#resources-calculation-for-scheduling-and-pod-admission). Reservations are not utilization or throttling measurements.

Collector identity failures now retain command stage, duration, timeout and collector UID. The five-second timeout and sampling cadence remain unchanged. A timeout still invalidates the affected evidence. Command wall time alone cannot distinguish authentication, process startup and API transport; the original pause remains causally unresolved. No shared identity cache or timeout increase was introduced without demonstrated safety.

## Interpretation

Report successful eligible windows separately from incomplete protocol coverage. Exact kernel/cache alignment excluded by the thesis is a limitation; genuinely missing required references remain insufficient. Resource comparison row counts include forwarded and repeated observations and must not be reported as loss percentages. A fixed-window numerical disagreement can coexist with complete settled accounting and exact API/storage parity.

The recovery does not provision cloud resources, republish images or authorize another run. A future launch requires new artifact hashes, placement review and budget approval.

## Boundary replay and strengthened operational checks

The isolated replay of the original error series with the pinned VictoriaMetrics image and original query returned 9, matching the API. Raw connector samples advance from 2 before load to 11 before the fixed endpoint, then 14 afterward. The 12 observed failures are accounted for after settling. This is a fixed-window emission-boundary discrepancy, not evidence of three errors lost in Karthos. Keep the original failure and report settled accounting separately. Individual increments cannot be attributed to trace IDs from these counters.

Before new generator Jobs and helpers, the harness server-dry-runs Pod templates to include admission defaults and injected containers, then checks CPU reservations against actual node allocatable resources and existing Pods. Placement checks include required node affinity, selectors and hard taints. Unsupported Pod-level resources or inter-Pod placement constraints fail closed. Capture changes additionally check admitted steady-state requests and conservative rollout surge before mutation. These checks supplement, rather than replace, actual rollout and placement verification.

Future collector identity failures retain HTTP request/response-stage markers and kubectl process identity. A credential-plugin wrapper records start, child creation, completion or cancellation separately, without credentials or response bodies. It uses an absolute script path so discovery from another working directory still works. The unchanged five-second timeout still invalidates affected evidence. These records distinguish an unfinished credential operation from a request reaching the API transport; they cannot guarantee identification of every underlying network or server cause. The historical sampler pause remains unknown.

## Targeted launcher profile

Use `prepare --schedule thesis-completion-v2 --matrix-profile astronomy-recovery-v1` with the ordinary explicit product lock, local qualification and configuration inputs. This freezes Astronomy trace smoke/baseline followed by Astronomy Istio smoke/baseline, on one new cluster. The manifest retains predecessor window IDs and hashes. It does not import previous passes or assert fresh two-cluster reproducibility. Without the profile, the original 14-window schedule is unchanged. Unknown profiles or edited matrix rows are rejected at runtime.

## Istio-only repair after the targeted Astronomy run

`prepare --schedule thesis-completion-v2 --matrix-profile astronomy-istio-repair-v1` freezes only Astronomy/Istio smoke60s and baseline300s, both k6 at2journeys/s. Set the same `execution_profile` in the configuration; the targeted approved combined ceiling remainsUS$240. The original14-window schedule and four-window Astronomy profile are preserved. Rows link to their failed predecessor IDs; historical passes are not imported into this new execution.

Setup records Deployment→ReplicaSet→PodUID ownership before observer changes. After rollout success it waits up to240seconds for those prior PodUIDs to disappear, writing a termination receipt before restoring the prior rollout strategy. Terminating Pods continue to count in capacity calculations. No force deletion, request reduction or measurement-window change is used. Two observer barriers add an explicit480-second setup reserve to the next planning estimate. Later Agent/capture capacity remains independently checked.

Live scoring and offline recovery now share immutable Pod volume declarations and conservatively timed lifecycle associations. Declarations require exact PodUID/node identity and resourceVersion evidence; they attest only immutable volume types, not invented source read times or cache versions. Conflicting declarations remain insufficient. A trace-inapplicable native settled mesh duration diagnostic is reported asnot-applicable; missing or failed applicable diagnostics retain their verdicts. This reporting fix does not convert the trace baseline's distinct population-boundary insufficiency into a pass.
