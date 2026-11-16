# Karthos evaluation

Standalone code and configuration for evaluating Karthos. Product code lives in its separate repository.

[Thesis completion v2](docs/thesis-completion-v2.md) is the **official thesis evaluation protocol**, not a rehearsal: two fresh GKE clusters, 14 serial k6 traffic windows, 38 minutes of scheduled traffic, checkout history and explicit source arbitration. It builds from an evaluation commit and does not require previous result bundles. The protocol guide contains the complete claim table and all 43 metrics, separating 18 selected comparisons from 25 contract/visibility checks.

- [Official protocol, architecture and claim/metric coverage](docs/thesis-completion-v2.md)
- [Frozen experiment definition](protocols/thesis-completion-v2/protocol.yaml)
- [Metric/reference definitions](protocols/thesis-completion-v2/metrics.yaml)
- [Catalog coverage](protocols/thesis-completion-v2/coverage.yaml)
- [Environment configuration](config/gke.completion-v2.example.yaml)
- [Compact exports, restoration and timing estimates](docs/export-efficiency.md)
- [Harness modules and verification](evaluation/README.md)
- [Terraform, permissions and cleanup contract](infrastructure/terraform/CONTRACT.md)

## Reproduce from a commit

A commit identifies the evaluation source and its frozen dependency/settings references. It does not guarantee identical latency, CPU readings or cloud-generated names. Build once and use the resulting immutable images for both cluster generations. Historical v1, repair, local-pilot and 46-window definitions remain available for interpreting their original evidence; do not mix their manifests into a new run.

### 1. Prepare the workstation and account

Use Git, Python 3.12 or newer, Docker with Linux/amd64 support, Google Cloud CLI, Terraform, and the pinned product's documented Go/Task/kind tools for local qualification. Controller dependencies are acquired from tracked pins by the build workflow. No service-account key file is needed: use user authentication locally and attached identities on cloud resources.

Check out the exact evaluation commit in a clean checkout. In the following commands, replace the uppercase example values with your own values; do not reuse another researcher's project, buckets, credentials or Terraform state.

```sh
git checkout --detach EVALUATION_COMMIT_SHA
export EVAL_PROJECT_ID=YOUR_GCP_PROJECT
export CLOUDSDK_CONFIG="$PWD/.work/gke/gcloud"
export GOOGLE_APPLICATION_CREDENTIALS="$CLOUDSDK_CONFIG/application_default_credentials.json"
mkdir -p "$CLOUDSDK_CONFIG"
gcloud auth login
gcloud auth application-default login
gcloud config set project "$EVAL_PROJECT_ID"
gcloud auth application-default set-quota-project "$EVAL_PROJECT_ID"
```

Create a dedicated project, attach billing and review the gross budget before enabling services or provisioning. Enable the APIs listed in the [infrastructure contract](infrastructure/terraform/CONTRACT.md). Verify quota for the three workers, controller and temporary bootstrap capacity, disk space and addresses. Quota availability is not a reservation of zonal capacity.

The operator needs permission to create the evaluation infrastructure/IAM, attach the dedicated service accounts and manage private bucket policies. Foundation creation and launch-specific IAM are explicit operator steps: follow the Terraform contract, review the plan, preserve remote state and record the resulting foundation reference. Controller and watchdog roles have distinct responsibilities; do not replace scoped permissions with project-wide Owner. Remove temporary launch grants after verified cleanup.

### 2. Build the committed artifact set

The build workflow uses the pinned Karthos revision and dependency references committed with the evaluation. It produces a new destination rather than overwriting another build. Run `./gke-eval.sh build --help` for options. If the pinned product repository requires authentication, grant Git read access or use `--product-repository` with an accessible local/SSH mirror; the exact revision and content hash are still verified. A public release must make that source accessible to independent reviewers.

```sh
./gke-eval.sh build \
  --revision EVALUATION_COMMIT_SHA \
  --destination /absolute/new/build-directory \
  --image-prefix us-central1-docker.pkg.dev/YOUR_GCP_PROJECT/YOUR_REPOSITORY \
  --build-images
```

The destination contains `source/` (the committed evaluation), `product/`, `source/cache/`, `toolchain/`, `build-receipt.json` and `product-lock.json`. The four built image tags use `commit-SHA12`. Publish the exact images identified by the receipt with explicit `docker push` commands, then record their registry digests:

```sh
python3 -B -m evaluation.gke.commit_build \
  --record-publication /absolute/new/build-directory/build-receipt.json
```

This creates `published-product-lock.json` and `published-images.json`. Use those immutable references when filling the environment configuration. Build and prepare from the committed `source/` directory, not a later edited checkout. Set `readiness.toolchain_path` to the built `toolchain/` directory. Mutable tags alone are not launch pins.

Building code is separate from publishing images or provisioning resources. Publish reviewed images to your own registry and use the resulting `@sha256:...` references. The original research project's private registry is not a required source of authority. Retain the build receipt and product/image lock with the launch package.

### 3. Qualify locally and prepare the manifest

Run the pinned local contract checks using the [local qualification instructions](docs/local-qualification-v2.md). The supported commands capture a new disposable kind fixture and execute the existing controlled Go/API helpers, then score their actual records into a checksummed receipt. Missing records remain insufficient; successful image builds or process exits alone cannot produce a passing receipt. Local fixtures establish controlled contracts, not cloud accuracy.

```sh
python3 -B -m evaluation.gke.local_kind_qualification \
  --product /absolute/new/build-directory/product \
  --product-lock /absolute/new/build-directory/published-product-lock.json \
  --out /absolute/new/kind-evidence \
  --cluster-name karthos-thesis-unique-lowercase-suffix

python3 -B -m evaluation.gke.local_qualification \
  --product /absolute/new/build-directory/product \
  --product-lock /absolute/new/build-directory/published-product-lock.json \
  --kind-records /absolute/new/kind-evidence \
  --vm-url http://127.0.0.1:YOUR_ISOLATED_VM_PORT \
  --out /absolute/new/local-contract-evidence
```

Use the generated `/absolute/new/local-contract-evidence/local-contract.json` for preparation. The local-kind command creates live containers and traffic; review its prerequisites and use a disposable local environment. It does not operate GCP.

Copy `config/gke.completion-v2.example.yaml` to an ignored private file such as `.work/gke/operator-config.json`. Fill all pending fields:

| Setting | What to supply |
|---|---|
| Project and foundation | Own project/billing identity, protected state reference and matching outputs |
| Location/version | Reviewed region/zone and exact available GKE Stable version |
| Images | Published immutable product, controller, watchdog, bootstrap and helper digests |
| Evidence | Own private primary, secondary and quarantine destinations; unique run prefix |
| Readiness | Verified billing, pricing, quotas, toolchain and account permissions |
| Forecast | Phase estimates including rebuild, export, history, retries and cleanup; gross prior spending and new projected cost |
| Local evidence | Exact-product qualification receipt and referenced artifacts |

Do not copy another run's `verified` flags without performing those checks. Preserve the scientific workload, source, timing and tolerance inputs. A changed experimental input requires a new recorded configuration and a new comparison scope.

```sh
./gke-eval.sh prepare \
  --schedule thesis-completion-v2 \
  --config .work/gke/operator-config.json \
  --id YOUR_UNIQUE_RUN_ID \
  --product-snapshot /absolute/new/build-directory/product \
  --product-lock /absolute/new/build-directory/published-product-lock.json \
  --local-evidence /absolute/new/local-contract-evidence/local-contract.json
```

Preparation is offline and lists unresolved inputs. Its output is `build/gke/prepared/YOUR_UNIQUE_RUN_ID/manifest.json`. Review the exact pins, 14-window schedule, claim coverage, permissions and full forecast. The approved budget is US$200 combined gross cost, with a US$90 projected launch threshold and 18-hour infrastructure limit. Credits do not reduce gross cost. The two-cluster forecast must fit before launch; a previous one-cluster estimate is insufficient.

### 4. Launch and inspect

After the operator has reviewed the concrete launch package, apply the documented launch-specific IAM and use the exact prepared manifest:

```sh
./gke-eval.sh provision --manifest build/gke/prepared/YOUR_UNIQUE_RUN_ID/manifest.json --run-id YOUR_UNIQUE_RUN_ID
./gke-eval.sh run --manifest build/gke/prepared/YOUR_UNIQUE_RUN_ID/manifest.json --run-id YOUR_UNIQUE_RUN_ID
./gke-eval.sh status --run-id YOUR_UNIQUE_RUN_ID
```

`run` starts the controller; deployment, minimal readiness, serial traffic, capture, exports and cluster rebuild are orchestrated there. The independent watchdog provides deadline cleanup. The operator need not manually start each window. In assisted execution, explicit chat approval precedes cloud launch; there is no approval-receipt command.

`status` reports saved local control state, **not live proof that the controller is running**. Inspect the actual controller container/process and provider resources when investigating a stall. A transport timeout does not prove execution stopped. Keep one active controller; preserve failures and use supported recovery rather than launching another concurrently.

### 5. Export, analyse and close

Completed windows export automatically. The commands below operate on a completed or partial run:

```sh
./gke-eval.sh export --run-id YOUR_UNIQUE_RUN_ID
./gke-eval.sh analyse --run-id YOUR_UNIQUE_RUN_ID --analysis-id YOUR_NEW_ANALYSIS_ID
./gke-eval.sh teardown --run-id YOUR_UNIQUE_RUN_ID
```

Primary exports do not certify secondary backup. Copy the sealed research objects to your configured secondary destination and verify each object's size and checksum/generation against the primary evidence index; record a receipt. Preserve private quarantine originals. Do not include credentials or Terraform state in research bundles. Keep the optional local-download cap (5 GiB) separate from full GCS retention.

Confirm provider-side absence of run-owned GKE clusters, controller VM/disks, networking/NAT, watchdog services and Scheduler jobs, and resolve the run lease and temporary IAM grants. Controller termination alone is not cleanup proof. Retain protected foundation, evidence, backup, registry and state resources. Raw evidence has 90-day retention with no automatic deletion; verify secondary export before any later deletion.

Original evidence remains in `results/` and GCS. Corrected interpretations go into a new `analyses/` directory referencing the original checksums. The final claim matrix distinguishes execution, evidence validity, application outcomes, numerical agreement and unconfirmed claims. Passing every metric is not required.

## Project layout

| Path | Purpose |
|---|---|
| `evaluation/` | Workloads, capture, independent references, verifiers and orchestration |
| `protocols/` | Versioned experiment rules, coverage and comparison definitions |
| `config/` | Account-specific configuration examples |
| `infrastructure/` | Terraform, controller packaging and independent cleanup watchdog |
| `docs/` | Protocol and operator documentation |
| `build/gke/` | Generated, checksum-bound input/build/review packages |
| `.work/gke/` | Private operational state, credentials and scratch space |
| `results/` | Original execution evidence |
| `analyses/` | Versioned interpretations linked to evidence checksums |

Research drafts and historical planning notes in `.plan-docs/` are ignored and are not runtime dependencies. Historical result bundles remain unchanged.

## Reanalyse preserved official evidence

See [official evidence recovery](docs/official-evidence-recovery.md) for checksum-bound offline commands, optional streaming-analysis dependencies, executed-versus-recovered API evidence, and the capacity/timeout safeguards. Corrected interpretations go into a new `analyses/` directory; original `results/` and cloud archives remain unchanged.

Controller report serialization, bounded failure recovery and memory-validation notes are documented in [controller evidence memory](docs/controller-evidence-memory.md).
