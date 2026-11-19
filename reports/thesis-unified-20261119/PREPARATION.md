# Unified final thesis evaluation — preparation

The user authorized this campaign on 19 November 2026 to replace the proposed four-window complement. It repeats all fourteen logical windows of `full-bounded-study-20261117` under one frozen revised evaluator. The old evidence and its reports remain unchanged. The new campaign is intended as the thesis's primary evaluation; it is not complete until new sealed results are acquired and checked. Failures and inconclusive claims remain reportable outcomes.

## Scientific scope

- Deployment 1: Boutique/traces, Astronomy/traces, Astronomy/Istio, Boutique/Istio, each smoke 60 seconds plus baseline 300 seconds (eight windows); both-source and replanned-trace arbitration, each 60 seconds (two windows).
- Deployment 2: Boutique/Istio and Boutique/traces, each smoke 60 seconds plus baseline 300 seconds (four windows).
- Preserve the existing 720-second temporal observation attached to the first deployment's Boutique/Istio baseline. It is not a fifteenth matrix window.
- Preserve Karthos `4c2861b539fcd24d6a458dc0fd545fbdda5470d5`, two journeys/second, scientific input hashes, image digests and tolerances. Freeze evaluator changes before launch.
- Apply independent inventory, source/provenance and independent-total checks; preserve unknown traffic, absolute errors, metric-family denominators, invalid attempts, and negative/inconclusive outcomes.
- No scale, causal-overhead, event/API latency, quantitative Kiali, additional consumer, Deployment rollout, reduced reproduction variant or LaTeX changes.

The updated campaign permits at most one operational retry per window and no retry for numerical disagreement. Exhausted operational attempts stop the campaign; remaining windows are unattempted. History is subject to the same stopping principle. A single campaign contains two distinct cluster deployments, with no overlap during rebuild.

## Cost and time preparation

The complete archived phase estimates, 6,000-second retry allowance and 3,600-second cleanup reserve give 66,120 seconds (18 h 22 min). Proposed deadline: 72,000 seconds (20 h). Estimates must not be shortened just to pass a launch gate.

BRL800 remains a reference only. BRL1,200 covers this entire additional effort, including the failed complementary preparation, export and cleanup; earlier original-study spending is outside that additional envelope. No credits are deducted.

The prior failed preparation receives a conservative **BRL450 allowance**, not a claim about the final invoice. It reserves USD70 at the margin-adjusted exchange rate, rounded upward. Its compute/network lifetime was under one hour, verified by provisioning and final provider-absence records; the allowance charges two hours at the transient bootstrap peak, preserves USD5 disk, USD8 NAT/network, USD15 logging, USD5 registry/watchdog/build, USD20 transfers, USD10 preparation contingency, and USD1 storage/operations allowances. The newly retained run/control/reanalysis objects total 53,428,974 bytes across 70 objects, including both evidence copies; the quarantine prefix is empty. Existing original raw evidence was not newly stored. These are a resource-based allowance and inventory observation, not observed billing charges.

Exchange rate: BRL5.8102/USD, BCB closing sale rate of 18 November 2026; 10% FX margin gives BRL6.39122/USD. New-campaign estimate charges twenty hours of four e2-standard-4 machines and GKE management, plus the previous full allowances for disk, network, logs, registry/build, storage, transfers and preparation contingency: approximately USD90.72 / BRL579.81. Together with BRL450 prior allowance and BRL150 additional cleanup reserve, the planning total is approximately BRL1,179.81. Derived launch limits and the full itemized estimate must be validated in the frozen manifest. Recheck if usage evidence or pricing changes.

Pricing references: [BCB](https://ptax.bcb.gov.br/ptax_internet/consultarUltimaCotacaoDolar.do), [Compute](https://cloud.google.com/products/compute/pricing/general-purpose), [GKE](https://cloud.google.com/kubernetes-engine/pricing), [Storage](https://cloud.google.com/storage/pricing). Provider-side invoice totals are not yet verified.

## Launch gates

1. Review and test the full-profile matrix, prior-spend deduction and stop policy, including history.
2. Rebuild the controller from the new fixed evaluator commit; preserve pinned product, toolchain, application and helper images. Reconcile prior deleted control objects through Terraform refresh, never blind recreation of the foundation.
3. Apply and read back the exact current-run conditional quarantine creator/viewer grants for the controller service account before provisioning. The evaluator must reject absent/mismatched grants before acquiring its lease or creating compute. Binding verification does not replace the actual controller upload/readback probe.
4. Verify account/project, scientific inputs, quotas, exact GKE version, budget, complete forecast and secondary destination. Arm and verify the independent watchdog before billable infrastructure.
5. Execute and observe the single fresh campaign. Preserve attempts and export seals, independently verify primary/secondary copies, remove current-run grants and temporary resources, and record final provider absence.
6. Produce a Portuguese report separating primary final-campaign evidence from earlier development/pilot campaigns. Do not fill final-campaign gaps with favorable historical results. Explain retrospective corrections and remaining limitations.

Status: the campaign started on 19 November 2026 at 04:28 São Paulo time, but both permitted setup attempts for the first smoke window failed before measurements. The first attempt was denied ClusterRoleBinding patch permission; the second failed during Helm uninstall. Zero measurement windows completed. The bounded retry policy stopped the campaign; the other thirteen windows remain unattempted. This execution cannot serve as the primary scientific evaluation. Both evidence copies and all 49 research objects per copy have been independently verified. All temporary resources were confirmed absent at 08:31:46 UTC, the lease was released, and temporary quarantine grants were removed. No additional campaign is started automatically. See `execution-status.json`; preparation and operational failure records are not scientific results.
