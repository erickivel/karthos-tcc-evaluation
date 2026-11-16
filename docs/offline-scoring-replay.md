# Offline scoring from a verified projected export

A downloaded normal raw bundle contains the privacy-projected `GKE-SHA256SUMS`,
which differs from the original controller-local seal. Do not substitute that
seal for `original_window_seal_sha256` or claim original-byte equivalence.

`evaluation.gke.prepare_scoring_replay` prepares a new, local workspace from one
already-downloaded raw archive. It makes no network calls and does not modify the
archive, receipt, manifest, or verifier source. The new workspace directories
use mode 0700 and retained files use mode 0600. It requires:

- The verified raw export receipt and its separately supplied SHA-256.
- The archive SHA-256 recorded in that receipt (`archive_parts == 1`).
- The unchanged measurement manifest and its separately supplied file SHA-256.
- The canonical implementation SHA-256 of the reviewed verifier source tree.
- A sealed `scoring-context.json` with the producer's canonical manifest hash,
  protocol, and `sealed-before-analysis-v1` pipeline declaration.

The last requirement deliberately excludes historical captures without this
producer binding. Multipart raw archives are also unsupported; neither case is
silently reconstructed or treated as an equivalent measurement.

```sh
python -m evaluation.gke.prepare_scoring_replay \
  /downloads/window.tar.gz /new/replay-workspace \
  --archive-sha256 ARCHIVE_SHA256 \
  --receipt /downloads/raw-export-receipt.json \
  --receipt-sha256 RECEIPT_FILE_SHA256 \
  --manifest /downloads/measurement-manifest.json \
  --manifest-sha256 MANIFEST_FILE_SHA256 \
  --source-root /reviewed/verifier-source \
  --verifier-sha256 VERIFIER_IMPLEMENTATION_SHA256
```

The command returns the path to `window/analysis/input-binding.json`. The verifier
implementation hash is `canonical_sha(implementation_hashes(source_root))`, using
`evaluation.gke.models`. Freeze/review it before preparation; source changes after
preparation invalidate the binding. A repaired verifier may differ from the
measurement-time implementation. Both implementations are retained separately;
the original measurement manifest and scoring thresholds remain unchanged.

The output binding is accepted by the Linux `evaluation.gke.scoring_worker`
entrypoint. Run it from the exact reviewed source root using the existing
`evidence_first.run_process` supervisor and a finite timeout. Preparation itself
does not run scoring, authorize traffic, or publish results. A directly invoked
worker's partial files are not a terminal analysis status: retain the supervisor
result and finalize `analysis-status.json` before treating outputs as complete.

Before and after scoring, `verify_inputs(..., projection_proof=...)` checks the
archive identity, retained receipt and manifest hashes, verifier hashes, archived
inner seal, every local input hash, and the producer's manifest binding. It also
checks the original-seal chain through `LOCAL-ORIGINAL-GKE-SHA256SUMS` and
`EXPORT-PROJECTION.json`. A newly generated local seal cannot replace that
archived authority. The original-local verification path remains unchanged.

Replay outputs identify `input_population: verified-projection` and
`original_byte_equivalence: false`. Privacy projection can remove information;
these outputs are versioned interpretations of preserved projected evidence,
not new measurements or proof that omitted original fields were recovered.
The archive must remain available at its bound local path for verification;
preparation does not duplicate it in the replay workspace.
