# Evaluation harness

This package contains the standalone evaluation implementation. It uses Karthos's existing discovery CLI, deployment chart and public API without modifying the product repository.

See the [current GKE execution plan](../docs/thesis-qualification-v3.md) and [frozen protocol](../protocols/thesis-qualification-v3/protocol.yaml) for the experiment schedule and operational commands.

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

The GKE runner reuses components validated by the earlier local pilots. Their retained execution contracts remain documented in [SIGNAL-CONTRACTS.md](SIGNAL-CONTRACTS.md), [PROTOCOL-4.md](PROTOCOL-4.md) and [TIMING-STUDY.md](TIMING-STUDY.md).

## Verification

```sh
python3 -B -m unittest discover -s evaluation/tests -q
```

Execution, evidence validity, numeric agreement and application outcomes are separate verdicts. Missing data remains insufficient evidence. Original execution artifacts live in `results/`; corrected interpretations belong in `analyses/`.

Dated findings, incidents, design drafts and repair narratives have moved to the local, Git-ignored `.plan-docs/evaluation/` archive. They are not required to run the harness.
