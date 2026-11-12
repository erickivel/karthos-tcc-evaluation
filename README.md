# Karthos evaluation

Standalone code and configuration for evaluating Karthos. Product code lives in the separate Karthos repository.

[Thesis qualification v3](docs/thesis-qualification-v3.md) is the active k6-only protocol: 13 readiness probes (1,020 scheduled seconds, up to three hours including a 20-minute cleanup reserve), then a 12-window, two-cluster measured matrix and a bounded 720-second history observation. Earlier protocol definitions remain available for analysis of sealed evidence; new preparations and launches accept v3 only.

- [Execution plan and architecture](docs/gke-qualification-v1.md)
- [Harness modules and verification](evaluation/README.md)
- [Frozen experiment rules](protocols/thesis-qualification-v3/protocol.yaml)
- [Metric/reference definitions](protocols/thesis-qualification-v3/metrics.yaml)
- [Catalog coverage](protocols/thesis-qualification-v3/coverage.yaml)
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
