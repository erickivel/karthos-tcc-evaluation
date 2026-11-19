# Complete repaired protocol with bounded lifecycle observations

The active thesis-completion-v2 protocol keeps 14 serial k6 windows, two fresh GKE
clusters, three workers per cluster and the separate controller. Traffic remains
38 scheduled minutes at two journeys/second. Readiness stays traffic-free.
Historical protocols, archives and verdicts are not rewritten.

## Evidence contract

- Traffic uses the recorded settled population as primary evidence, with raw
  paired-source, histogram, quiet-endpoint and export-coverage guards. Exact
  API/storage parity and original fixed-window diagnostics remain separate.
- The existing checkout history phase additionally saves lifecycle-reference.ndjson,
  lifecycle-api.ndjson and lifecycle-trigger.json.
- Independent Kubernetes reads retain acquisition times, Pod UIDs/resource
  versions, node placement, readiness, container IDs, restart states and ordinary/
  restartable init-container states. Environment variables are not copied.
- Graph observations retain acquisition times, snapshot identity, request/page
  timings and the actual service/workload/Pod/node placement and health answer.
  Pod names alone cannot establish a matching answer.
- The reported trigger-to-first-verified-snapshot interval is a conservative
  controller-monotonic elapsed bound. It includes rollout, materialization,
  polling and transport. The reference/API acquisition offset is a separate
  observation interval, not an exact ingestion delay.
- Same-name replacement without API runtime-UID evidence, missing resource
  versions, unavailable snapshots, uncertain associations and nonmatching health/
  placement remain insufficient.

The new records reuse existing Kubernetes and API history calls. No new traffic,
watch service, high-frequency sampler or product API is introduced. They stay
inside the existing 720-second history observation/export budget and its existing
sub-budgets; the conservative historical phase forecast is unchanged.

~~~mermaid
flowchart LR
    A["Independent old Pod: UID + version + state"] --> B["Recorded restart request"]
    B --> C["Independent replacement: UID + version + state"]
    C --> D["First matching healthy placed API snapshot"]
    D --> E["Historical answer correctness + bounded elapsed observation"]
~~~

## Boundaries and launch

The bounded-lifecycle-observation claim is independently reported and requires
verified history export, safe completion and correct historical consumer answers.
It does not upgrade exact host/cache alignment or fixed-window membership.
No Karthos product change is part of this implementation.

The next launch must build/publish the complete repaired harness, review effective
collector settings and per-node capacity, refresh account/version/IAM/budget
checks, and receive explicit launch approval. Keep 18 hours, US$90 projected launch
threshold and US$270 combined gross ceiling. Prior expenditure is not reset by
backing up or starting a fresh study.

The retained historical study plus repair evidence remains usable as a combined,
qualified analysis. It is not relabelled as one unchanged successful execution.
