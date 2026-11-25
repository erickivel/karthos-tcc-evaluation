# Thesis repair validation v2

This is a fresh protocol epoch. The definitions and results of `thesis-repair-validation-v1` and older protocols remain unchanged and readable. The six logical windows reference `thesis-repair-09a2330-20261113` as provenance; no predecessor pass or continuation is imported.

One fresh cluster runs six serial k6 windows at two matched journeys per second:

| Order | Cell | Smoke | Baseline |
|---|---|---|---|
| 1 | Boutique / Istio | 60 seconds | 300 seconds |
| 2 | Astronomy / Istio | 60 seconds | 300 seconds |
| 3 | Boutique / trace | 60 seconds | 300 seconds |

There is no second cluster, history observation, native load, Locust, or synthetic readiness workload. Per-cell readiness remains read-only. Each logical window has at most two attempts; numerical disagreement is a result and never a score-seeking retry reason. A safe baseline may proceed independently of smoke accuracy. The unchanged limits are 12 hours including cleanup, USD80 projected launch ceiling, USD100 combined study budget including prior runtime and shared costs, 90-day retention, and a 30-minute artifact recovery allowance.

## Prepare offline

Copy `config/gke.repair-v2.example.yaml` to a private configuration, supply the reviewed product lock and local contract evidence, complete cumulative cost and timing estimates, and replace the quarantine prefix with the exact new preparation/run ID:

```sh
./gke-eval.sh prepare --schedule thesis-repair-validation-v2 \
  --config /absolute/private/config.json --id NEW-RUN-ID \
  --product-snapshot /absolute/product-snapshot --product-lock /absolute/product-lock.json \
  --local-evidence /absolute/local-contract.json \
  --origin-report /absolute/origin/run-report.json --origin-seal /absolute/origin/GKE-SHA256SUMS \
  --origin-uri gs://EVIDENCE/gke-qualification-v1/runs/thesis-repair-09a2330-20261113/final-summary/run-report.json
```

Use a valid lowercase identifier in place of `NEW-RUN-ID`. Preparation renders local manifests and records pending prerequisites; it creates no cloud resources, traffic, permissions, or approval receipts. Present the exact new manifest, product/image locks, costs and scoped IAM proposal to the operator for explicit launch approval. Earlier launch approval does not apply to this epoch.

Both runner configuration and manifest must actively declare `capture_finalization_version: 2` and `servicegraph_store_max_items: 10000`; historical baseline configuration cannot activate these fields. Preview and live planning pass the explicit product flag `--servicegraph-store-max-items=10000`. Before trace traffic, generated and live desired configuration must match; the v2 capture reader also verifies the actual original ConfigMap volume inside the same instrumented gateway Pod, bound to its UID. Collector bounds are parsed as durations and must equal ascending 100ms, 250ms, 500ms, 1s and 5s; store TTL is 30s and flush interval is 15s. Running image specifications and image IDs must match reviewed immutable digests. A mismatch aborts; the harness never changes settings to obtain a passing comparison.

The setup `effective-settings.json` records generated settings and desired ConfigMap identity. Its `mounted_file_proof` remains explicitly pending until the v2 capture lifecycle verifies the same-pod reader mount. A desired ConfigMap alone is not mounted-file proof.

## Quarantine prerequisite

The protected foundation reference must include the new private versioned 90-day quarantine bucket output. Old foundation references remain interpretable, but cannot authorize v2 launch. Configure `quarantine.bucket` to that output and `quarantine.prefix` to exactly `gke-quarantine-v1/runs/<new-run-id>/`. A separate narrow IAM proposal grants the current controller principal object create/get only under this run prefix. Preparation records `remote_permissions_verified: false`; no fabricated review flag or machine approval receipt substitutes for explicit scoped review. Do not apply the foundation or IAM during this preparation review.

## Independent result families

Detailed reports, bounded orchestration summaries, attempt rows and matrix HTML expose traffic count, traffic error, traffic duration, exact API/storage parity, resource accuracy, timing validity, and application outcomes separately. Each family uses existing scored checks and source-population gates. A demonstrated failure remains a failure even if an unrelated catalog metric or timing evidence is missing. Missing source population proof remains insufficient; exact API/storage parity does not establish source accuracy. No global pass is invented and tolerances and denominators are unchanged.

## Operator credentials and cleanup

The wrapper and Python command contexts default `CLOUDSDK_CONFIG` to an isolated `.work/gke/gcloud` directory and `GOOGLE_APPLICATION_CREDENTIALS` to its `application_default_credentials.json`. Explicit environment values are preserved, including for Terraform and emergency cleanup. To use a separately prepared isolated context:

```sh
export CLOUDSDK_CONFIG=/absolute/private/gcloud-context
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/private/operator-adc.json
./gke-eval.sh status --run-id NEW-RUN-ID
./gke-eval.sh teardown --run-id NEW-RUN-ID
```

The bootstrapped controller preserves attached-VM metadata ADC rather than manufacturing an operator credential file. These paths must already contain valid operator credentials; no command changes global gcloud user configuration. New v2 live preflight reads the active/impersonated CLI principal and ADC type, checks service-account principal agreement, and records the explicit context. User ADC does not expose its principal in the file; the preflight records this limitation rather than claiming identity equivalence. The selected project is checked independently against the manifest.

Cleanup remains available after the measurement deadline. Ownership, UID, project and saved-inventory checks still apply. Errors retain stage, exception type, exit status and at most 1200 sanitized stderr characters. Exact stderr is retained only in private mode-0600 files under `.work/gke/<run-id>/command-diagnostics`; it is excluded from evidence exports. A partial teardown keeps residual inventory and independent cleanup controls visible.


## Optional local review downloads

Full GCS evidence exports, required controller readbacks, retention and backups are unchanged. The existing `export` command uploads evidence; it is not a local downloader. V2 explicitly requires `limits.optional_local_download_bytes: 5368709120` (5GiB). Optional operator downloads use the supported command:

```sh
./gke-eval.sh download --manifest /absolute/prepared/manifest.json --run-id NEW-RUN-ID \
  --object gs://PROJECT-karthos-eval-evidence/gke-qualification-v1/runs/NEW-RUN-ID/final-summary/run-report.json \
  --destination /absolute/private/review/run-report.json
```

The destination parent must exist and the destination must be new. Only one object in the current run's primary evidence bucket is accepted; wildcards, recursion, other runs and raw quarantine are rejected. The command reads object size and generation first, rejects unknown metadata, atomically reserves its full size in `.work/gke/<run-id>/optional-downloads.json`, then downloads that immutable generation without decompression. The SDK retry count is zero for this optional operation; manual retries make new reservations. Concurrent commands share a file lock and cannot over-reserve the cumulative5GiB allowance. Failed, interrupted and in-flight reservations remain charged because partial data may already have crossed the network. They are never automatically refunded. A successful receipt records generation, bytes and local SHA256; `status --run-id ...` includes bounded usage totals. This cap counts downloaded object payload bytes; it does not reconstruct historical billing, HTTP metadata/header bytes or earlier manual downloads.

The cap applies only to this optional operator command. Mandatory uploads, same-region verification readbacks and scientific evidence remain uncapped by it. Direct ad hoc gcloud downloads bypass the supported policy and must not be used to claim compliance with the reviewed5GiB allowance. Earlier incurred transfer and gross costs remain separate, unverified accounting inputs.

Quarantine creation uses the GCS JSON API exact-object media upload with `ifGenerationMatch=0`, streamed by bounded curl. Google Cloud SDK567 `storage cp` performs destination-list discovery on a missing object, which would require bucket-wide list permission; that permission is deliberately not granted. The token is supplied on stdin, and metadata plus generation-pinned full SHA256 readback remain unchanged. Reference: https://docs.cloud.google.com/storage/docs/json_api/v1/objects/insert.
