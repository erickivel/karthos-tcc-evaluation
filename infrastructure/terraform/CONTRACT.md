# Offline infrastructure contract

Only the approved evaluation orchestrator may plan/apply/destroy these roots.
This document defines the infrastructure contract, not live provisioning status. All roots use Google provider **7.29.0**, an exact
constraint and generated `.terraform.lock.hcl` checksums. Use
`terraform init -backend=false -lockfile=readonly` for offline verification.
Live init requires a reviewed private GCS backend, per-root prefix and locking:
`foundation`, `controller`, `cluster`. Never export backend configuration,
credentials, state, `.terraform/`, or plan files into evidence bundles.

The dedicated project and billing attachment are supplied by its owner. Required
APIs (each with the .googleapis.com suffix): cloudbilling, cloudresourcemanager, logging, compute, container, storage, artifactregistry, run, cloudscheduler, iam, iamcredentials.
The read-only provisioning preflight checks this set before creating resources and names any missing API. Controller startup separately checks its attached identity and attempts one bounded Logging write. HTTP status and allowlisted provider reasons are retained; credentials and arbitrary error payloads are not.
For the initial bootstrap only, the approved orchestrator may create foundation
using private local state and immediately migrate it to protected GCS state;
subsequent roots and operations must use per-root remote locking. An existing
state bucket can instead be imported before the first remote-state apply. Use ADC/workload identity, never service-account keys. Operator must have
permission to enable APIs, create resources/roles, attach service accounts,
manage GCS IAM and deploy authenticated Cloud Run. Review IAM and budgets first.

## Root inputs and outputs

Common inputs: `project_id`, `region` (fixed default `us-central1`).

| Root | Additional required inputs | Outputs |
|---|---|---|
| foundation | `state_bucket_name` (pre-existing, import first) | `evidence_bucket`, `state_bucket`, `artifact_repository`, `controller_service_account`, `node_service_account`, `watchdog_service_account`, `scheduler_service_account` |
| controller | `zone` (fixed default `us-central1-a`), `owner_id`, `run_id`, `controller_image`, `watchdog_image`, `controller_boot_image`, `evidence_bucket`, `controller_service_account`, `watchdog_service_account`, `scheduler_service_account`, `state_bucket`, `manifest_uri`, `manifest_generation`, `manifest_sha256`, `inventory_object`, `inventory_generation`; optional `watchdog_enabled=false`, `runtime_enabled=false`, `bootstrap_context_uri=""`, `bootstrap_context_generation=""` | `network`, `subnetwork`, `controller_name`, `controller_uid`, `watchdog_uri`, `router_resource_name`, `watchdog_service_name`, `scheduler_name` |
| cluster | `zone` (same fixed default), `owner_id`, `run_id`, `network`, `subnetwork`, `node_service_account`, `kubernetes_version` | `cluster_name`, `cluster_resource_name`, `cluster_endpoint` (sensitive), `node_pools` |

Images require `@sha256:<64 hex>`; no default image exists. The controller boot
image is a reviewed explicit Compute image name with docker, gcloud, containerd
and required host tooling preinstalled. No mutable image family is accepted.
Build inputs must be prepared under `build/gke/bundles/<experiment-id>/`:

```
source/                 exact reviewed evaluation/infrastructure/protocols/config/docs + README.md/gke-eval.sh
source-manifest.json    {"files":{"relative/path":"sha256"}, ...metadata}
product/                reviewed product source archive; no .git/host worktree pointers
product-manifest.json   same exact checksum-map shape
cache/                  reviewed boutique/opentelemetry-demo/karthos-chart caches
cache-manifest.json     exact map; empty allowed only when readiness is pending
toolchain/manifest.json platform=linux/amd64, files map, versions map
```

Dockerfile is `source/infrastructure/controller/Dockerfile`; its build context
must be this bundle directory. Required build args are `BASE_IMAGE` immutable
`@sha256:...`, `SOURCE_MANIFEST_SHA256`, `PRODUCT_MANIFEST_SHA256`,
`CACHE_MANIFEST_SHA256`, `TOOLCHAIN_MANIFEST_SHA256`. All checksums pin raw manifest
bytes; each manifest must cover the exact file closure. No symlinks are accepted.
The source verifier requires every implementation subtree and both entrypoints.
No build, binary download, package fetch or publication is part of offline prepare.
Missing toolchain, caches, lockfiles or images remain explicit readiness gaps.

Toolchain layout: executable Linux ELF amd64 `bin/{kubectl,helm,istioctl,terraform,task,gke-gcloud-auth-plugin}`,
`go/bin/go`, complete `google-cloud-sdk/` (including bin/gcloud and VERSION),
`requirements.lock`, and offline `wheels/`. The versions map requires nonempty
pins for kubectl/helm/istioctl/terraform/task/go/gcloud/gke-gcloud-auth-plugin; all distribution files,
including SDK/runtime libraries and wheels, must be covered by checksums.
Python lock must pin PyYAML==6.0.3, google-auth[requests], functions-framework and every
transitive dependency using pip hashes. Installation uses --no-index and
--require-hashes. Host MachO/ARM binaries are rejected. The reviewed base must
supply Python at /usr/local/bin/python, bash, git, curl, CA trust and the native
libraries these reviewed tools need. Build checks tool versions without cloud
access; runtime must record actual tool output/version and image provenance.
No key files are copied. Gcloud uses the attached metadata identity and SDK path;
CLOUDSDK_CONFIG is /data/gcloud. ADC receives no host credentials volume.

Packaged reviewed code lives at /opt/evaluation, verified product at /opt/product
and caches under /opt/evaluation/cache. Container root is read-only; runtime
must create mutable workspaces and outputs under GKE_EVAL_DATA_ROOT=/data,
including writable copies of product/cache inputs when tools generate files.
GKE_EVAL_PRODUCT_MANIFEST=/opt/product-manifest.json identifies approved archive
checksums; source manifest is /opt/source-manifest.json. Read-only source is never
used for .work/results. Host /var/lib/karthos-evaluation/data binds to /data,
retaining work/results, gcloud config, kubeconfig and Go caches across process
crashes. Authorization occupies its own read-only mount. The runtime container
has a fixed name, manifest/component labels and persistent data/controller.cid;
it is retained on stop/crash. Startup refuses to replace an existing cidfile;
inspect status/logs and use reviewed recovery rather than silently rerunning.
VM/disk destruction still requires durable GCS checkpoint export beforehand.

The VM has no external address; its subnet reaches registries through temporary
Cloud NAT. The zonal Standard cluster uses private nodes and private endpoint.
Controller and cluster state are independent: destroying cluster leaves VM and
network intact. Final approved teardown must remove controller root, including
NAT, Scheduler and Cloud Run. Foundation remains protected. Cluster master can
be upgraded by GKE despite min_master_version; capture actual master/node
versions per generation and reject changed qualification conditions.

## Manifest handoff and watchdog inventory

VM startup downloads the generation-pinned experiment manifest and checks its SHA-256 for transfer integrity. The existing runtime-enabled flag keeps provisioning dormant until the separate run operation. Runtime consumes `--manifest` and checks reproducible source/configuration inputs and technical readiness. There is no approval receipt, review-ID command or software human-approval gate. Codex waits for the user's explicit go-ahead in the conversation before invoking cloud operations.

The Cloud Run watchdog uses attached keyless identity; only Scheduler SA has
roles/run.invoker. No unauthenticated cleanup endpoint exists. The standalone Terraform default pauses Scheduler; the approved orchestrator
first creates only the external watchdog, invoker IAM and enabled Scheduler with
pinned creation intents, before creating any private network/NAT/VM. Handler
normal requests enforce deadline cleanup; authenticated dry-run is per-request
X-Karthos-Dry-Run: true, never a persistent production switch. The orchestrator
verifies the exact candidate inventory and enabled Scheduler before compute. Rebuilds require a fresh
inventory generation and service update before traffic; never concurrently
recreate resources while an old armed inventory remains in use.

Inventory JSON (epoch seconds, deadline no more than 43200 seconds after start):

```json
{"project_id":"dedicated-project","owner_id":"research","run_id":"qual-1",
 "started_at":1800000000,"deadline":1800043200,
 "resources":[{"kind":"cluster","name":"projects/dedicated-project/locations/us-central1-a/clusters/cluster-name","uid":"GKE-REST-id"},
              {"kind":"instance","name":"projects/dedicated-project/zones/us-central1-a/instances/controller-name","uid":"Compute-instance-id"}]}
```

Terraform `cluster_resource_name` is a path, **not** GKE UID; capture UID from
Container REST GET `id` and confirm ownership labels `evaluation-owner` and
`evaluation-run`. Compute output `controller_uid` is the numeric instance ID.
Handler ignores request bodies, reads only configured GCS generation, enforces
project/name/kind/UID/ownership and maximum deadline, then starts asynchronous
cluster, controller, inventoried disk and router/NAT cleanup. Router ownership
is encoded in its JSON description (GCE routers do not support labels); capture
its immutable numeric REST GET `id`, never its Terraform `.id` resource path.
The adapter deletes the UID-verified owned router directly, which also deletes its attached NAT gateways. It avoids a separate NAT patch requiring network-policy updates. Disks require
matching `evaluation-owner` / `evaluation-run` GCE labels and numeric REST UID,
including dynamically created PVC disks; inventory must be refreshed and pinned
when those disks are created. No project-wide blanket deletion is performed. Missing resources are idempotent.
Scheduler checks every five minutes; this introduces up to five minutes delay
plus cloud deletion time. APIs lack atomic UID deletion preconditions: a second
UID/ownership check narrows the race. Keep the old armed revision active during
explicit deletion; after confirmed absence, pin and activate the next creation
intent before any new API creation. Never pause deadline coverage for rebuild. Refusal or
partial deletion must alert and block further execution. This code does not
claim hard real-time deletion or complete final nonbillable network teardown.

The watchdog cannot delete projects, buckets, state, IAM, registry, or arbitrary
resource types; its custom IAM role permits cluster/instance/disk/router
get/delete, operation get and Scheduler pause. Its pre-existing router-update permission is retained but is not used by cleanup; network-policy update permission is not added. It
reports residual/error resource names and continues independent deletions on
partial API failures. It pauses its configured Scheduler only on a later tick
confirming every inventoried resource is absent; Cloud Run remains cold with
zero minimum instances. The controller root still needs final orchestrator
teardown of the cold service, scheduler, firewall, subnet and network. Missing
inventory entries remain a readiness failure, not proof of complete cleanup. It is independent of chat, controller process and GKE workloads.

## Evidence retention and prerequisites

Evidence bucket is private, uniform access, versioned, prevent_destroy, no
force_destroy, 90-day minimum retention. There is **no automatic deletion
lifecycle**: age alone never proves safe export. The controller has creator and
viewer rights, no raw-evidence delete rights. Raw deletion after retention
requires a separate reviewed operator procedure verifying a secondary export
manifest (object generation, bytes, checksum, count, successful re-read), then
explicit deletion; this infrastructure provides no unchecked deletion helper.
State bucket is separately protected and must never be exported.

References verified for implementation: [provider 7.29.0 cluster schema](https://registry.terraform.io/providers/hashicorp/google/7.29.0/docs/resources/container_cluster),
[retention semantics](https://docs.cloud.google.com/storage/docs/bucket-lock),
[Scheduler OIDC](https://docs.cloud.google.com/run/docs/triggering/using-scheduler),
[Cloud Run IAM](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
Runtime quota, available GKE versions, connectivity, permissions, image/toolchain,
watchdog delivery/deletion, IAM/RBAC and export restore remain **unverified live**.

Watchdog HTTP responses are JSON, including refused methods and internal errors.
They carry `not_hard_guarantee=true` and explicit residual/unchecked inventory;
requested asynchronous deletion is not reported as proven resource absence.
Only a subsequent successful read proving all absent permits Scheduler pause.

## Bounded names and policy enforcement

Owner/run identifiers are lowercase letter-led and at most 48 characters. Full
identity remains in `evaluation-owner` / `evaluation-run` labels, router JSON
ownership and evidence manifests. Resource names use deterministic SHA256
suffixes: cluster name `karthos-eval-` plus 20 hex characters from
`sha256(jsonencode([owner_id, run_id]))` (33 total); controller network prefix
uses 16 hex characters from `sha256(owner_id)` (29 total), with bounded suffixes
for VM, firewall, service and Scheduler. The controller/network names remain
stable across cluster run IDs. Consume Terraform outputs; do not derive names
from labels or treat the name digest as immutable cloud UID. Offline tests
execute the actual Terraform local expressions with maximal identifiers.

The cluster explicitly uses `LEGACY_DATAPATH` with `network_policy.enabled=true`,
provider `CALICO`, and its NetworkPolicy addon enabled. This enforces the
API-consumer namespace policies rather than leaving inert Kubernetes objects.
[Google's policy documentation](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/network-policy)
describes enabling both addon and enforcement. GKE Dataplane V2 was considered,
but its [documented custom eBPF restriction](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/dataplane-v2)
conflicts with assuming OBI is supported there; therefore it is not selected for
this qualification. Calico policy enforcement introduces its own fixed system
components, which must be captured in baseline overhead and environment records.
Live policy allow/deny probes and OBI kernel/runtime compatibility still remain
required qualification checks; Terraform schema validity proves neither.

## Lifecycle entry points

`evaluation.gke.provision.provision(ctx)` checks actual project/billing identity,
required enabled APIs, available CPU quota and exact server/node versions before
mutation. It privately bootstraps foundation and migrates state into its protected
bucket; all later Terraform roots initialize locked remote state. It uploads
create-only reviewed authorization objects, creates dormant controller/network,
precreates GKE through Cloud API without local Kubernetes endpoint calls, pins
observed cloud UIDs, tests authenticated watchdog dry-run and activates its
Scheduler. It returns provisioned, never runtime-complete. Existing bootstrap
state/buckets require explicit inspected recovery rather than blind retry.

`start_controller(ctx)` verifies the inventoried VM UID and armed watchdog, writes
handoff intent first, sets runtime-enabled and generation-pinned
bootstrap-context-uri/bootstrap-context-generation metadata, then requests a VM
graceful stop/start. A second request is refused even after ambiguous transport errors.
Startup remains dormant unless runtime-enabled is
true; then it downloads the bootstrap object and passes its read-only path in
GKE_EVAL_BOOTSTRAP_CONTEXT. The object contains sanitized Terraform outputs,
cloud UID inventory, deadlines and control-object generations, never tfstate,
private keys or sensitive cluster endpoint output. The prepared experiment manifest remains the reproducibility record; approval is conversational.

`create_cluster(ctx,1)` inside the VM verifies/reuses the precreated cluster UID
and resolves credentials into its explicit private kubeconfig. Repeat 2 retires
the old inventory, destroys and recreates only the cluster root, pins new actual
UIDs and verifies watchdog activation before traffic. Only two generations are
allowed. `refresh_inventory(ctx)` must also run after labeled PVC disk creation.
`arm_watchdog` deploys an authenticated tagged revision with --no-traffic, tests
its exact inventory generation using a per-request dry-run header, then switches
traffic only after verification. The old armed revision and Scheduler remain
active throughout. `disarm_watchdog` refuses unless every inventoried resource is
confirmed absent and no creation intent is unresolved. Rebuild never calls it.
Neither path grants public invoke.
OIDC dry-run uses short-lived impersonation of the invoker-only Scheduler SA,
with token passed via stdin, never argv or evidence. Runtime's controller role
has cluster lifecycle, network observation/use, private state-object access and
narrow watchdog-update permissions, plus node/watchdog SA attachment. Scheduler
OIDC minting is scoped to its service account. The local approved operator needs
corresponding permissions independently; no key files are provisioned.

`evaluation.gke.teardown.destroy_cluster(ctx)` preserves the controller.
`teardown(ctx)` verifies all existing resource UIDs/ownership before destruction,
never destroys foundation, and leaves independent watchdog/control resources
available when cluster/disk cleanup is partial. It removes billable VM/NAT/router
first, observes absence, then removes cold control resources. The optional manual full-root path runs from the approved local CLI; the
independent completion path below removes temporary compute/network and pauses
the timer from outside the VM. A VM cannot verify its own complete deletion. Return values report residual resources/errors and do not
assert a hard deletion guarantee. Permission/transport errors are never treated
as NOT_FOUND. Concurrent recreation remains prohibited during checks/deletion.

Before blocking creation, a generation-pinned inventory contains an exact
creation intent. Controller/network/NAT/VM intents are pinned before their apply;
the external watcher and Scheduler are established first using a targeted apply
that excludes all private compute/network resources. Cluster intents are pinned
and activated before its apply. A lost accepted API response therefore remains
inside independent deadline scope. No blanket project discovery deletion exists.

Emergency teardown uses the saved target/inventory and does not depend on an unexpired
experiment deadline: `load_cleanup_context(root, run_id, executor=...)` loads only
an existing private `.work/gke/<run-id>/execution.json`, its saved cleanup config
and canonical target fingerprint. It cannot manufacture a run, provision, arm or
start runtime. Its executor accepts cleanup commands only; each cloud deletion
still verifies the saved exact ownership and UID. Provision freezes recovery
config before mutations. Saved pinned creation intents can recover an accepted
resource through its immutable first-UID binding; legacy unpinned creations and
never-observed intents stay explicit residuals, never completed cleanup.
This is an accidental-target safeguard over trusted local private state, not a
cryptographic defense against an attacker controlling the operator's filesystem.

## Independent completion cleanup after sealed export

After the frozen `gke-qualification-v1/runs/<run-id>/final-summary/` export verifies,
run calls `request_final_cleanup(ctx, export_receipt)`. It independently captures
GCS generations and SHA256 for GKE-SHA256SUMS and every sealed summary object,
including run-report.json; pins a fresh inventory containing all owned cloud
UIDs; and activates the external watchdog on a generation-pinned completion
request. An unverified receipt, wrong run/bucket/prefix, missing seal, checksum
mismatch or incomplete network inventory refuses early cleanup. Creation of the
request is not a deletion-completion claim. Control objects stay under the
separate control/<run>/ prefix.

The watchdog re-reads these exact export generations and verifies checksums
before acting before the deadline. A boolean alone is insufficient. The ordinary
12h deadline path still works when completion proof is unavailable. It deletes
only UID/owner/run-matching compute and inventoried disks; their absence must be
observed before router/NAT cleanup. Successive invocations confirm router absence,
then remove owned firewall, subnet and network in dependency order. GCE network,
subnet and firewall ownership is encoded in JSON descriptions with numeric REST
UIDs; resource names, labels and mutable fingerprint alone are insufficient.
In-use/permission failures remain explicit residuals and retry on the independent
schedule. No project, bucket, registry, IAM, or unrelated resource deletion exists.

Every non-dry-run observation is written create-only to
control/<run>/teardown/<unique>.json by the keyless watchdog's bucket-scoped
roles/storage.objectCreator grant. Proof includes target/run/inventory generation,
observed-absent names, residuals, errors and `compute_network_absence_verified`.
Only observation of every inventoried resource absent permits Scheduler pause;
a second durable result records `scheduler_paused=true`. If that final GCS write
fails, the watchdog attempts to resume its Scheduler so persistence can retry.
The finalizer runs outside controller/GKE and continues without a chat or live
controller. This replaces dependence on a later human command for temporary
compute/network cleanup and timer pause.

The Cloud Run service **definition** remains and is configured with zero minimum
instances; foundation remains protected. The cleanup invocation itself is an
active request, so this code reports `active_instances_verified=false` rather
than claiming simultaneous verified zero active instances. Service-definition
removal remains an optional explicit local cleanup after durable results are
read. It has no always-on instance configured. Local execution state is request
history, never external proof; status must read matching immutable GCS teardown
observations and actual Scheduler state before claiming cleanup confirmed.

## One active run per dedicated project

Before controller/network or cluster creation, the approved orchestrator acquires
`gs://<protected-state-bucket>/control/active-run.json` using a generation-zero
create precondition. The lease records exact project/owner/run, a random token,
phase and deadline. It has no automatic expiry takeover: a stale/failed run must
finish verified cleanup or undergo explicit inspected recovery. A different run
is refused even with two otherwise valid prepared manifests. Shared Terraform state
locking and local controller locks are supplementary, not cross-host ownership.
Initial protected foundation creation precedes the lease because its state bucket
does not yet exist; existing-bucket bootstrap is refused rather than assumed
unowned. No billable controller/GKE resources are launched before the lease.

The lease travels in generation-pinned bootstrap and inventory objects. The VM,
Mac CLI, Terraform mutation path, repeat creation, handoff and watcher activation
re-read it before mutation. Different token/run or changed generation refuses
new work. Cleanup can refresh the observed generation only for the same saved
run/token. The deterministic controller lookup also refuses a foreign owner/run
or an unexpectedly replaced saved UID before controller apply.

Final cleanup CAS-marks the lease `cleanup-requested`, blocking stale contexts
from recreating resources while the independent finalizer runs. Deadline fallback
still recognizes the same ownership token across that phase update. The watchdog
holds the lease while deleting owned UIDs, observing complete compute/network
absence, pausing its Scheduler and recording that proof. It CAS-deletes the lease
last using the freshly observed generation. It performs no Scheduler action after
release, because another run may then own that shared service name. A final GCS
write failure after release retains the prior absence/timer-pause observation;
status must inspect lease state rather than infer from local request history.

Watchdog state-bucket IAM grants only `storage.objects.get/delete` conditioned on
that exact lease object. It cannot read tfstate or list/export the state bucket.
The state bucket itself remains protected. Confirmed local full teardown likewise
releases its same-token lease after absence; partial cleanup never releases it.
Cloud UID checks and lease checks are immediate preconditions, not a distributed
transaction with Compute/GKE APIs: concurrent out-of-band mutation remains
unsupported and ambiguous transport always requires observed recovery.

## Creation intents, immutable first UID and uninterrupted delivery

A creation intent has `{kind, name, intent_id, binding_object}`; intent_id is a
fresh 32-hex identity and binding_object is exactly
`control/<run>/uid-bindings/<intent_id>.json`. Names are canonical project-scoped
paths and the shared project lease prevents controlled concurrent recreation.
Before the creation API call, the intent is uploaded and its tagged watcher
revision is verified and made active. This includes accepted calls whose client
times out before Terraform can return outputs. The initial watcher-only targeted
apply occurs before network/NAT/VM creation, closing the unarmed VM bootstrap gap.
If that first watcher establishment fails, no evaluation compute/NAT has started.

At the deadline (or verified final completion), the external watcher reconciles
only pinned intents. It checks the lease and exact owner/run/name, observes the
first actual cloud UID, and persists that UID as a create-only object with
ifGenerationMatch=0 before requesting deletion. A racing observer must read and
agree with the same binding. Later observation of a replaced UID refuses cleanup;
the binding is never overwritten. The normal controller and saved-inventory
cleanup use the same binding rule, so a successful apply cannot silently replace
a watchdog-observed UID. A dry-run creates no binding object and deletes nothing.

An intent never observed in cloud remains unresolved: 404 is not proof that an
accepted asynchronous creation can never appear. It remains a residual and prevents timer pause/lease release, retaining a watch
for late arrival. A successfully observed-absent compute intent does not itself
block independently owned router/NAT or network cleanup. Actual present compute
and failed observations still gate dependent network operations; in-use network
API refusals remain explicit residuals and retry normally. Bound
resources can be deleted even when Terraform never returned an output; stale
Terraform state is separately flagged for reconciliation. Legacy unpinned intents
remain explicit inspected-recovery cases. Failed binding writes retry safely;
a committed observation survives caller or controller death.

Revision replacement uses tagged --no-traffic deployment, authenticated
X-Karthos-Dry-Run on that URL, exact inventory-generation verification, then an
explicit 100% traffic switch checked against Cloud Run status. A process death
before that switch leaves the prior active revision serving. Scheduler is not
paused and persistent DRY_RUN=true is never used during inventory refresh or
long Terraform cluster deletion. An authenticated caller may ask for one dry-run;
ordinary Scheduler requests always retain normal deadline semantics.

Authoritative contracts: [Cloud Run tagged traffic migration](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration),
[gcloud no-traffic revision update](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update),
[GCS generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions).
Runtime rollout, IAM propagation and scheduled delivery remain live prerequisites;
offline fixtures prove control decisions, not cloud availability or hard real time.

Controller boot disk is explicitly 300 GB; each of the three workers stays 50 GB.
This provides space for the 100 GiB raw budget, a second archive/projection copy,
OS/tools and headroom. Startup creates persistent /data/tmp and passes
TMPDIR=/data/tmp, so temporary export archives use that boot disk rather than the
RAM-backed /tmp mount. Reviewed source/product remain read-only. Live startup must
still measure at least 220 GiB free before accepting the full bounded run; nominal
disk size is not a free-space or quota measurement.

Official thesis-completion-v2 inventories explicitly set `infrastructure_seconds: 64800` for the approved 18-hour limit. The `thesis-robustness-full-v1` profile additionally records `execution_profile: thesis-robustness-full-v1`; only this explicit marker permits a positive integer limit up to 86,400 seconds (the reviewed full-run configuration uses 72,000 seconds). Without that marker, explicit limits remain restricted to 43,200 or 64,800 seconds. Legacy inventories without the limit field retain the 43,200-second cap. The pinned watchdog validates the declared limit and never extends the immutable recorded deadline.


The controller VM requests both `cloud-platform` and `userinfo-email` OAuth
scopes. GKE otherwise authenticates a VM service account by numeric unique ID,
which does not match email-based RBAC subjects. This scope does not grant RBAC
permissions: deployment must still verify the actual controller's authorization
for Helm installation, updates and removal on every cluster generation. See
[GKE VM identity and RBAC](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/role-based-access-control#forbidden_error_for_service_accounts_on_google_cloud_vm_instances).

The watchdog's custom cleanup role includes `compute.networks.updatePolicy`
in addition to `compute.firewalls.delete`: the live provider rejected deletion
of the owned firewall without the network-policy permission on 19 November 2026.
The UID, ownership and lease guards still restrict cleanup decisions. This source
correction is not proof that a reused foundation's IAM role was updated; reusing
foundation outputs requires verifying the effective deployed permissions. The
failed `thesis-robustness-full-20261119` campaign used the earlier frozen image and
was cleaned with an identity-checked operator fallback for this firewall.
