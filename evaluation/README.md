# Evaluation harness

This package contains the standalone evaluation implementation. It uses Karthos's existing discovery CLI, deployment chart and public API without modifying the product repository.

See the [official protocol](../docs/thesis-completion-v2.md) and the [frozen experiment definition](../protocols/thesis-completion-v2/protocol.yaml) for the experiment schedule and operational commands.

## Modules

| Area | Files |
|---|---|
| GKE coordination | `gke/` — preparation, infrastructure, setup, scheduling, evaluation, export and cleanup |
| Controlled workloads | [workloads/](workloads/README.md) — active k6 recipes and ledgers; historical adapters retained for evidence interpretation |
| Independent references | [references/](references/README.md) — kubelet, host and filesystem observations |
| API-only consumer | `consumers/api_only.py` |
| Boundary capture | `structured_capture.py`, `timing_capture.py`, `receiver_input_proxy.py` |
| Generator execution/export | `generator_execution.py`, `generator_artifacts.py` |
| Comparison semantics | `series_identity.py`, `evidence_v4.py`, `scoring.py`, `timing_analysis.py` |
| Regression tests | `tests/` |

The GKE runner reuses components validated by earlier local runs. Their measurement contracts are documented in [SIGNAL-CONTRACTS.md](SIGNAL-CONTRACTS.md).

## Verification

```sh
python3 -B -m unittest discover -s evaluation/tests -q
```

Execution, evidence validity, numeric agreement and application outcomes are separate verdicts. Missing data remains insufficient evidence. Original execution artifacts live in `results/`; corrected interpretations belong in `analyses/`.

## Narrow observation and duration contracts

For Istio, `families.traffic_duration` is the primary **matched settled-population**
comparison against the last stored samples inside the declared drain export.
It uses the existing population, histogram synchronization, unit and numerical
checks, and also requires successful recorded export-horizon evidence. Missing
settled evidence never falls back to a fixed-window pass. Trace-source duration
continues to use its existing population verifier. `fixed_window_traffic_duration`
retains the original selected fixed-window accuracy; `exact_api_storage_parity`
remains independent. This changes report interpretation, not endpoints, source
measurements, tolerances, or original sealed reports. Offline reinterpretations
carry `accuracy-evidence-v3.3.0`.

`bounded_observations` describes host and lifecycle observation coverage, retaining
exact comparison counts separately. An OTLP timestamp denotes collection, not a
kernel read or cached Kubernetes object version. Numeric values across dimensions
and units are not pooled into a host range. Missing exact read/cache alignment
continues to produce insufficient accuracy evidence.

For matching Pod and Node identities (and the container identity when provided),
non-overlapping independent Kubernetes acquisition brackets can show a reference
state change. `bounded_observation.transitions` records the outer interval from
the earlier acquisition start to the later acquisition end in Unix seconds.
These are observation intervals, not exact event timestamps, collector detection
latency, proof of a single transition, or gauge accuracy. Receiver-input snapshots
are excluded from independent history. Restart decreases are descriptive state
changes, never monotonic counter proof. Each detailed comparison retains at most
16 intervals with explicit total and omitted counts; exact evidence is unchanged.
