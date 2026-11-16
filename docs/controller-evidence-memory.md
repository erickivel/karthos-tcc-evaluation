# Controller evidence memory

The evaluation controller and Karthos are separate processes on separate machines. A controller out-of-memory failure does not establish metric loss in Karthos. Preserve the previous checkpoint and recover raw attempt files before deleting its disk. Offline recovery is a new analysis, never a replacement for the original execution verdict.

## Bounded report serialization

`evaluation.gke.models.write` streams JSON to a temporary file in the destination directory, then atomically replaces the destination only after encoding succeeds. It retains sorted keys, two-space indentation, Unicode escaping and the final newline. An encoding failure removes the partial file and preserves the previous report. This avoids retaining the encoder's complete chunk list plus the joined report string in RAM. It does not discard observations or change comparison tolerances.

The new regression tests cover byte equivalence and failed-write preservation. A controlled 128 MiB report produced identical SHA-256 with 469 MiB peak RSS for the original writer and 15.5 MiB for streaming on macOS. These are synthetic serialization measurements, not a complete live-run memory bound.

## Host reference lookup

Host reference candidates are indexed by immutable node UID, reference source and metric family. Existing comparators still check exact node names, dimensions, units, lifecycle, duplicates, conflicts and time alignment. The overall expected reference count remains explicit. This removes repeated scans over unrelated metric families; it does not turn absent source timestamps into agreement.

## Operator recovery and limits

Check container exit/OOM status and serial logs rather than treating a RUNNING VM as a live evaluation. SIGKILL bypasses Python exception handlers and ordinary cleanup. The independent infrastructure watchdog remains required. IAP recovery requires a narrowly scoped TCP 22 ingress rule from 35.235.240.0/20 to the controller; remove temporary recovery rules during cleanup. Cloud log forwarding also requires the controller identity's logging permission. Never publish uninspected recovered originals to ordinary evidence: preserve them privately in quarantine with generation-bound checksum readback.

Do not enlarge the controller solely to mask whole-report serialization. Measure the repaired replay's peak RSS and leave operating-system/export headroom before deciding whether 16 GiB remains sufficient. Publishing and launching repaired artifacts requires a new reviewed manifest.

Volume reference lookup is additionally indexed by Pod UID, metric, volume and exact underlying measurement timestamp. Full identity, unit, lifecycle and conflict checks remain in the comparator. Duplicate measurements are preserved. Regression fixtures retain conflicting values and nanosecond-distinct times.

## Error-path reads

Failure recovery reads the bounded orchestration summary instead of loading the detailed JSON report. Any resulting recovery interpretation is written to `recovery-verdict.json`, preserving the existing detailed file. Cleanup errors remain in the authoritative `diagnostic-cleanup.json`; the cleanup handler does not decode and overwrite the detailed measurement report. This prevents a second memory failure while handling an operational error.

The operator `analyse` command also uses the checksum-verified streaming analysis projection for reports above 8 MiB, instead of decoding the entire expanded report. Install the existing pinned `requirements-analysis.txt` in the operator environment. Matching-reference bodies and availability payloads are omitted only from the in-memory interpretation; original archives remain authoritative.

## Archived baseline validation

The repaired scorer completed the retained Astronomy/Istio baseline on the same pinned Linux image with a 6 GiB container limit and 3.456 GiB peak RSS, writing all 3,927,982,804 report bytes. The original-code replay was killed during full-string JSON encoding. This supports retaining the existing controller size for this case; it is not a universal memory bound for every future workload.

The streaming operator reader additionally verified the archived 991,217,240-byte smoke report with every family verdict unchanged, at roughly 142 MiB peak RSS on macOS. The original archive was not modified. See the versioned controller-memory-recovery-v2 analysis for checksums and cleanup receipts.
