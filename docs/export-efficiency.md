# Compact metadata publication and export forecasts

The official 14-window scientific matrix is unchanged. This update changes publication packaging and records costs separately; it does not shorten pre-roll, drain, quiet checks, capture finalization, load, history or recovery allowances.

## Publication and evidence references

Readiness and attempt checkpoints are privacy-projected, sealed, packed into bounded `window*.tar.gz` parts and published with `window-index.json`. The normal archive privacy/path/expanded-size inspection still runs. Every object keeps generation-zero creation and complete download/checksum verification; transfer concurrency remains two. A failed verification prevents a successful receipt.

Window receipts now bind published object hashes/sizes and the original/projected hashes of selected finalized reports. Later checkpoints reference those verified archives instead of copying the same full reports. A missing/unbound legacy receipt or a changed local report uses the previous explicit retention behavior. Failed/unexported attempts still retain available finalized diagnostics. References preserve access to large reports that exceed the small-checkpoint JSON-copy bound; they do not make those reports valid scientific evidence.

Checkpoints deliberately keep small graph/consumer snapshots and compact summaries. These are the minimum self-contained inputs needed by existing continuation and cross-cluster comparisons. Raw telemetry remains in the original window archive, with all original status/timing dimensions and missing-data verdicts unchanged. A checkpoint cannot justify deletion of its referenced window archives; include those archives in the secondary export and existing retention policy.

To restore a downloaded checkpoint bundle, first verify the publication receipt/object checksums. Then use the safe unpacker and the existing continuation validation:

```python
from pathlib import Path
from evaluation.gke.export import unpack_metadata_bundle

unpack_metadata_bundle(Path('/absolute/downloaded-checkpoint'),
                       Path('/absolute/new/extracted-checkpoint'))
```

The unpacker checks both seals, indexed archive hashes/sizes, privacy and member paths, rejects duplicate members and symlinks, and refuses an existing destination. Pass the extracted directory and original publication receipt to the existing `prepare --continuation-dir ... --continuation-receipt ...` interface. That validation still checks protocol/product/implementation/scoring bindings; unpacking alone is not approval or a successful continuation.

## Forecast categories

The official example now requests four export estimates:

| Key | Included work |
|---|---|
| `readiness_export` | Readiness projection, packing, publication and readback per deployment group |
| `window_export` | Full scientific window projection, packing, publication and readback |
| `checkpoint_export` | Compact checkpoint publication after each window, history and final checkpoint |
| `final_export` | Final summary publication once |

All four must be positive finite reviewed estimates. Older configurations containing only `export` retain the old formula; providing a partial new set fails closed. The uninterrupted official matrix has eight deployment groups, fourteen window exports, sixteen checkpoints and one final summary. Additional failed attempts use the existing bounded retry reserve; the deadline can still leave work unattempted.

The new `readiness-export`, `checkpoint-export`, `window-export` and `final-export` timing records measure publication boundaries. Do not add nested `upload`, `readback` and `publication-transfer` timings to their enclosing totals. Receipts retain their completed export timing so final publication is not confused with earlier measurements.

## Offline replay

`python -m evaluation.gke.metadata_benchmark --archive ... --out ...` reads preserved archives without modification, verifies every member against the internal seal, and reconstructs representative readiness payloads and progressively larger checkpoints. It alternates three baseline/candidate trials for each input, verifies unchanged readiness/retained-graph member hashes, and records implementation/input hashes, CPU/wall time, object/command counts and transferred bytes.

Its transport emulator exercises immutable writes and full readback locally; it never contacts GCS. Therefore it demonstrates packaging/round-trip correctness and operation/byte reduction, **not cloud end-to-end speed**. The original standalone readiness receipts were not available in these input archives, so reconstructed payloads must not be described as measured historical readiness runs.

Retain the full-window export allowance unless its complete cloud path is measured or otherwise defensibly bounded. Local tar/compression speed alone cannot establish that a 12-hour launch will fit. The generated review under `build/gke/` records the revised conservative estimate and unresolved launch gate.
