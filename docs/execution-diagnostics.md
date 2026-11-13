# Supplementary execution diagnostics

`evaluation.gke.execution_diagnostics.ExecutionDiagnostics` collects debugging
evidence for explicitly supplied evaluation namespaces. It is independent of
metric accuracy, workload outcomes and required-oracle capture. A failure here
is recorded and does not turn another metric into a passing result or suppress
an original workload exception.

```python
collector = ExecutionDiagnostics(ctx, window_path / 'execution-diagnostics', namespaces)
collector.start()
try:
    run_window()
finally:
    diagnostic_status = collector.stop()
```

Stop before sealing or uploading the directory. `stop()` returns `stopped`;
when false, the worker is still finishing bounded reads and the directory is
not safe to seal yet. Call `stop()` again before exporting. A context manager
is also available and never suppresses the body exception.

The target cadence is five seconds; each iteration records its actual duration.
Pod inventory includes UID, placement, resources, conditions and normal, init
and ephemeral container states. Newly observed terminated previous instances
trigger an immediate bounded previous-log read. Current logs use a round-robin
queue (at most two per iteration), so current-log coverage is best effort rather
than a claim that every container was read every five seconds. Missing Pods and
identity changes are explicit. A post-read identity check records whether a log
can be attributed; raced logs remain supplementary, explicitly unattributed.
Logs retain immutable incremental chunks per concrete instance, capped at 2 MiB in total. Current-log reads resume at the last complete, attributable timestamp using an inclusive cursor. Chunk receipts explicitly mark possible boundary overlap; identical text or timestamps are never silently deduplicated. Partial lines and failed/unattributed responses do not advance the cursor. Each chunk has a SHA-256 digest. Reaching a request or per-instance byte bound records a coverage gap and never replaces earlier chunks. Runtime log rotation can still remove text between polls; these supplementary records do not claim lossless log coverage.
The entire collector has a 256 MiB output budget; saturation stops collection
and is explicit in `diagnostics-status.json`.

Events are sampled at collector start and stop. The controller record includes
process CPU and peak RSS, load, disk space, Linux memory/process-I/O snapshots
and direct child PIDs where available. These are descriptive execution records,
not independent application-resource references. Raw logs pass through the
existing privacy projection and checksummed export path. Events, previous logs
and container runtime rotation can be incomplete; missing previous instances,
request failures, truncation and cadence are retained rather than inferred away.

Required raw capture has a separate completion receipt in `structured/status.json`:
`workers_stopped` is derived from sampler-thread liveness and each log worker's
actual stopped/liveness state; `proxy_stopped` is derived from the private proxy
process return codes after the capture stack closes. `completion` lists the
observed workers and applicable proxy PIDs. These fields do not imply valid
telemetry: sample errors remain independent. No applicable proxy means there
was no private proxy process to stop, not a proof about telemetry availability.

### Final-summary retention

Already verified window archives are referenced by receipt instead of copying their supplementary diagnostic logs again. Stopped, unexported fatal diagnostics can be retained once within the collector bounds. Missing/corrupt termination records produce explicit unavailable markers and exclude mutable streams. Small finalized reports and transitions are retained independently of raw-capture termination, with separate evidence scope.

## Candidate append-only required capture

Evaluation file exporters use append=true, flush_interval=1s and no rotation. This preserves fixed timing-tail offsets and avoids deleting old required records at the previous 64 MiB rollover. The existing owned 512Mi emptyDir eviction threshold and 512 MiB expanded archive validation remain unchanged; emptyDir is not an instantaneous per-write hard limit. Exhaustion, eviction, partial JSON, capture restarts and missing streams remain invalid evidence. This candidate has fixture validation only and must be versioned and published before any later launch. The currently running controller and historical verdicts are unchanged. Legacy rotated archives still fail closed unless a separately reviewed completeness proof is supplied.

Verified upstream: https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.156.0/exporter/fileexporter/README.md (append and rotation cannot be combined; rotated writes are unbuffered).


### Supplemental archives and restricted failed snapshots

Stopped diagnostics without a verified window export are redacted and privacy
checked before being packed with the bounded window archive machinery. Their
`diagnostic-archive-index.json` records each projected member's SHA-256 and byte
length, archive part hashes, and explicit `log-index.ndjson` coverage gaps.
Verified windows retain an export reference and do not duplicate diagnostic
chunks in the summary. Neither form changes scientific validity.

The repair validation protocol may configure `quarantine.bucket` and
`quarantine.prefix`, with the exact prefix `gke-quarantine-v1/runs/<run_id>/`.
The bucket must match the foundation `quarantine_bucket` output and differ from
the normal evidence bucket. The separate private bucket preserves originals for
at least 90 days with unlocked retention, versioning, public access prevention,
no automatic deletion, and Terraform destruction protection. It has no permanent
controller grant: `evaluation.gke.quarantine.preflight_requirements(ctx)` emits a
reviewable current-run conditional create/get IAM proposal and expected bucket
policy; it does not apply IAM or claim remote permission verification.

An explicit failed collector status with a source UID classifies only matching
`structured/.../raw-attempt-N/`, `raw-files-attempt-N.tar`, or a failed canonical
`raw/` capture. These originals are uploaded unchanged with generation-zero
creation and generation-pinned checksum readback. Normal redacted exports carry
only original relative path, hash, byte count, source UID, bounded measurement
bounds, fixed failure category/status, and verified URI/generation receipts.
No raw tail or arbitrary exception/status text enters receipts. Archive path,
regular-member, and expanded-size checks still apply. Quarantining an invalid
required canonical capture records `scientific_evidence_valid: false`; it cannot
repair measurement validity. Upload/readback failures stop publication and leave
originals in place. Unclassified malformed evidence remains fatal; callers that
omit the optional `export_projection(..., quarantine=callback)` retain strict
historical behavior.
