# Minimal per-cell readiness

`evaluation.gke.minimal_readiness.check_cell(ctx, driver, spec, attempt_id)` observes the deployment already prepared by the normal runner. It returns `usable`, independent `checks`, a local receipt path, and a verified export receipt. It creates no traffic, helpers, resource diagnostics, restarts, sampler capture, or reset workloads.

Observation is bounded to 120 seconds and the remaining infrastructure budget minus cleanup reserve. Kubernetes/API calls are bounded individually; catalog and graph reads inspect one page and make no completeness claim. The existing verified, privacy-projected export runs after observation and remains subject to infrastructure export/deadline safeguards.

Required observations are ready active application and Karthos pods, current rollout generations with all desired replicas available, the expected request-metric source in the deployed `karthos-source-contract` ConfigMap exactly matching the resolved local plan, a nonempty catalog response and a nonempty graph response. Resource metric presence is informational: an empty query is `unavailable`, never a fabricated zero or an automatic traffic failure. Accuracy and source traffic delivery belong to the measured smoke/window.

Each observation failure is retained independently; later checks still execute while time remains. Core check failures return `usable: false`; the matrix owns continuation and recovery. An unverified receipt export raises `MinimalReadinessSafetyError`. Every attempt uses a distinct ID and evidence prefix, preserving failed attempts. Credentials and unprojected operational state are excluded by the existing export mechanism.
