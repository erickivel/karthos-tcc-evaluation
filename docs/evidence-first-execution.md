# Evidence-first execution

New completion configurations select `evidence_pipeline: sealed-before-analysis-v1`.
Historical definitions without this setting retain their original execution path.

The attempt stops its workload and capture, restores instrumentation, joins diagnostic
workers and verifies the completion barrier. It then seals, projects and publishes
raw evidence with immutable object creation and full checksum readback. Only after
that receipt is verified does a separate Python process compute the extended GKE
report. Existing timing/oracle validation still runs during collection; this change
isolates extended scoring, not every operation that interprets a sample.

The Linux scorer has a 6 GiB RLIMIT_AS (virtual address-space) limit. This differs
from a container RSS limit; both are exercised in the offline replay. Scoring may
fail without repeating traffic. The parent preserves its explicit analysis failure,
exports worker diagnostics and continues only through the existing safety/budget
barriers. It never describes pending analysis as valid numerical evidence.

Raw archives are not uploaded again. The analysis bundle records the raw export
receipt; the run index retains separate raw and analysis receipts. Comparison JSONL
and checksum-addressed reference records preserve ordered occurrences and conflicting
values. Compact-format readers validate every sidecar. If privacy projection changes
reference bytes, publication fails instead of publishing invalid reference hashes.

The offline `analyse` command resolves the separately restored analysis directory.
Accuracy and claim recovery accept `--analysis-archive` and `--analysis-sha256` alongside
the raw archive. The two-archive reader is read-only and verifies both physical
checksums and the cross-bundle binding. Multipart raw inputs require an explicit
multipart reader and fail closed in this single-archive recovery interface.

No scientific duration, tolerance or collection cadence is changed. Duration primary
accuracy uses matching settled populations; fixed-window fidelity stays separate.
Host/lifecycle descriptive observations do not become exact kernel/cache accuracy.

Operational monitoring remains external, every 900 seconds. It does not interpret
VM RUNNING as proof that the scorer/controller is alive. Temporary IAP access is
ownership-scoped and removed with a receipt. Cloud logging tests the attached
controller identity. The independent deadline watchdog remains the cleanup backstop.

The controller runtime locks `ijson==3.4.0.post0` and checks its import during image build. Terraform locks include verified Linux and Mac provider hashes. Run image unit tests with `GKE_EVAL_DATA_ROOT` unset so each fixture keeps its isolated temporary directory.
