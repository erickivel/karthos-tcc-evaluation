# Karthos evaluation

Standalone code and configuration for evaluating Karthos. Product code lives in the separate Karthos repository.

[Thesis qualification v4](docs/thesis-qualification-v4.md) is the active k6-only protocol: read-only per-cell readiness, 21 independent planned windows across two thesis cluster lifetimes and a later diagnostic launch, a bounded history observation, and one retry for recoverable execution failures. Each attempt stops capture and verifies cleanup before heavy export. Historical v2/v3 and 46-window definitions remain available for sealed-evidence analysis. No cloud execution starts without launch approval.

- [Execution plan and architecture](docs/gke-qualification-v1.md)
- [Harness modules and verification](evaluation/README.md)
- [Frozen experiment rules](protocols/thesis-qualification-v4/protocol.yaml)
- [Metric/reference definitions](protocols/thesis-qualification-v4/metrics.yaml)
- [Catalog coverage](protocols/thesis-qualification-v4/coverage.yaml)
- [Environment configuration](config/gke.example.yaml)
- [Infrastructure contract](infrastructure/terraform/CONTRACT.md)

## Execution

`gke-eval.sh` provides prepare, provision, run, status, export, analyse and teardown operations. Codex prepares the files, explains the setup in chat, and waits for your explicit go-ahead before cloud work. No review commands or approval receipts are required.

```sh
./gke-eval.sh --help
python3 -B -m unittest discover -s evaluation/tests -q
```

## Project layout

| Path | Purpose |
|---|---|
| `evaluation/` | Workloads, capture, references, verifiers and runners |
| `protocols/` | Experiment rules consumed by the harness |
| `config/` | Environment and resource configuration |
| `infrastructure/` | Terraform, controller and cleanup watchdog |
| `docs/` | Current operational documentation |
| `build/gke/` | Generated inputs and execution bundles |
| `.work/gke/` | Private operational state |
| `results/` | Original execution evidence |
| `analyses/` | Versioned interpretations referencing original evidence |

Research drafts, historical planning notes and explanatory briefs are kept locally in `.plan-docs/`, which is ignored by Git. They are not runtime dependencies. Historical result bundles are preserved.

Targeted product/harness repair validation: [protocol and operator guide](docs/thesis-repair-validation-v1.md). This named six-window run preserves the original qualification definitions and requires separate launch approval.
