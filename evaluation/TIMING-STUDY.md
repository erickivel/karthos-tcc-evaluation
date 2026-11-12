# Four-cell timing study (timing-1.4)

This is a diagnostic successor to protocol4. Original scores and artifacts stay
unchanged. All code/evidence belong to this separate evaluation project;
Karthos product behavior and public APIs are unchanged.

## Commands

From the evaluation root:

```bash
python3 -m evaluation.timing_pilot plan
python3 -m evaluation.timing_pilot analyse results/<timing-study-id>
./timing-pilot.sh --run-id timing-<unique-id>
```

The wrapper loads the explicit existing remote-k3s profile and validated isolated
product worktree. The monitor launches `timing_pilot run --run-id ...`; direct
use requires the same explicit environment. Analyse refuses historical roots.

## Frozen design

Four cells run serially: Boutique traces, Astronomy traces, Boutique mesh,
Astronomy mesh. In each cell: two120s probes, then two300s matched confirmation
windows at2journeys/s. Generator order alternates by cell. There are16planned
windows and56minutes scheduled traffic. One confirmation per generator/cell is
a diagnostic confirmation, not a new repeated performance benchmark.

Each probe reuses checkout and expired-payment recipes. Twenty-four checkout
operations (one per recipe at offsets20,29,31,44,46,59,61,74,76,89,91,100s)
follow48prerequisite requests. The fixed inner interval is[30,90), with the full
probe[0,120) also reported. Actual event times determine membership; missed or
late schedule execution is retained. Probe requests have10s maximum timeouts,
and no new HTTP request may start after120s. Locust uses one process/user with
24scheduled workers; k6 uses24once-only VUs. These are probe controls, not the
arrival models of the unchanged matched confirmations.

Before traffic, confirm node/local timestamp bound is strictly below250ms.
Calibrate observed counter cadence but do not assume phase locking from nominal
configuration. Preserve60s preroll,90s drain and two fresh unchanged counter
observations separated by the measured interval, bounded at90s. Source capacity,
flush/collection intervals, workloads and numerical tolerances stay frozen.

## Two accuracy outputs

1. **Eventual completeness:** fresh quiet source-partition deltas and reset
   evidence, compared descriptively with independent observed calls/errors.
2. **Fixed-window fidelity/timeliness:** both start/completion populations,
   emitted/stored timestamps, exact API query parity, counter brackets and
   first-observed availability intervals. Duration means remain non-equivalent
   unless matching populations/sums/counts are established.

Every window retains both populations. Inferred proxy start time includes
proxy timing/measurement uncertainty. A datapoint timestamp is never labelled
an ingestion timestamp. Recorded acquisition brackets bound observed visibility;
the five-second polling cadence is nominal, and actual request/receive times are retained.

Availability capture polls raw VM samples, three paginated public API metrics,
and incremental collector metrics/traces. It preserves full dimensions and
source. Recorder `fixed-evaluation1` revisits the identical evaluation timestamp
until visible or expired after 90 seconds, then opens a new probe. API range-query
step stays 15 seconds; duration window stays 60 seconds. This measures API query
visibility including VictoriaMetrics' recent-point policy, not ingestion latency
or finality of the returned value. Only negative responses to the identical query
bound a positive response. Empty expired points remain empty evidence; required
streams still need positive observations. See [the root-cause and repair notes](../.plan-docs/evaluation/local-pilot-history.md#api-availability-probe-correction-2026-10-24).

Collector reads use binary subprocess streams and SHA256-framed snapshot chunks.
Non-UTF8 stderr is retained separately from the payload. Up to three transfer
attempts may retry the identical file offset within the existing five-second
poll budget, retaining failed response bytes and attempt errors. A recovery is
counted explicitly, and only checksum-verified complete bytes advance offsets.
Malformed source JSON/UTF8, lifecycle changes, exhausted retries or cadence
failures still invalidate required evidence. Source-format failures are not
retried. This distinguishes transport corruption from malformed collector data.

The independent kubelet sampler uses `bounded-kubelet-json2`: binary stdout
and separate stderr, retained malformed responses, and up to three read-only
attempts. The first attempt keeps its existing five-second timeout; retries are
allowed only while the initial one-second nominal polling window remains.
Only valid JSON with the summary envelope and selected node identity becomes
a sample. The successful attempt owns acquisition timestamps; underlying
kubelet measurement timestamps are never replaced. Exhaustion remains an error,
and existing sampling cadence/gap and coverage thresholds remain unchanged.
Every successful poll retains the complete parsed summary and local body hash.
Ordinary successes do not duplicate the response as base64. Failed attempts and
their successful retry retain raw bodies for diagnosis. Storage limits, growth
factor and expected capture duration remain unchanged. Local body hashes do not
claim an API source-side digest.

Each file read records pod UID/inode/offset and raw bytes; incomplete
JSON, truncation/rotation/restart, missing sources or stopped workers are explicit
failures. Existing gzip storage bounds, checksums and complete-before-ACK remain.
Availability workers must stop before capture restores instrumentation. If a
worker cannot be confirmed stopped, archive the failure and retain the independent
ownership-checked watchdog rather than restore under an active reader.

## Gates and deadline

Offline preparation verifies previous qualification, source pins and historical
evidence, publishes hypotheses, and reanalyses the six incomplete windows in a
new directory. Their start/completion totals are equal; that hypothesis alone
does not explain the original differences. Historical clock bounds remain unknown.

The new four-hour clock starts at live preflight after offline preparation.
Reserve export/restoration before every new window. Stop on invalid required
oracle/source/capture, ownership/safety/deadline failures. Numerical disagreement
is diagnostic evidence, not a reason to resize or retry. No automatic reruns.
The idle stopped generator may keep its existing30minute artifact-recovery hold.

After core windows, a separate agent diagnosis is limited to15minutes and only
attempted with enough export reserve. It uses the preserved failing prompt once,
records the pinned replay/fallback source, independent observer and raw trace,
and never introduces a replacement fixture/retries. It does not gate checkout
accuracy; non-recurrence does not prove the original301cause.

## Outputs

`run-report.json` is live controller progress. Each window has
`timing-verification.json`, fixed `intervals/whole` and `intervals/inner` exports
for probes, full recorder status, original-compatible confirmation verification,
and checksums. `timing-summary.json` uses `report-references1`: each entry points to its full
per-window `timing-verification.json` with a SHA256. It does not duplicate all
raw observations into a second multi-gigabyte aggregate; full reports and raw
evidence remain intact. `SUMMARY.md` and `gke-decisions.json` retain
supported findings, unresolved timing questions, and provisional GKE choices.

No cloud provisioning/pricing/scale claim follows from this single-node study.
Keep both sources and tools; five minutes remains provisional until timing and
footprint evidence justifies a narrower protocol. No causal overhead claim is
made without a comparable uninstrumented control.
