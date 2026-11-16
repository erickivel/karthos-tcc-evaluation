# Dedicated project must already exist with billing attached by its owner.
# No project/billing-account resources and no automatic evidence deletion.
resource "google_storage_bucket" "evidence" {
  name                        = "${var.project_id}-karthos-eval-evidence"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  retention_policy {
    retention_period = 7776000 # minimum 90 days; deletion also requires verified export
    is_locked        = false   # locking is irreversible and requires separate human review
  }
  versioning {
    enabled = true
  }
  lifecycle {
    prevent_destroy = true
  }
}
resource "google_storage_bucket" "state" {
  name                        = var.state_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true
  }
  lifecycle {
    prevent_destroy = true
  }
}
resource "google_artifact_registry_repository" "evaluation" {
  location      = var.region
  repository_id = "karthos-evaluation"
  format        = "DOCKER"
  lifecycle {
    prevent_destroy = true
  }
}
resource "google_service_account" "runtime" {
  for_each     = toset(["controller", "node", "watchdog", "scheduler"])
  account_id   = "karthos-eval-${each.key}"
  display_name = "Karthos evaluation ${each.key} (keyless)"
}
resource "google_project_iam_custom_role" "watchdog" {
  role_id     = "karthosEvaluationWatchdog"
  title       = "Karthos evaluation deadline cleanup"
  permissions = ["compute.instances.get", "compute.instances.delete", "container.clusters.get", "container.clusters.delete", "container.operations.get", "compute.disks.get", "compute.disks.list", "compute.disks.delete", "compute.routers.get", "compute.routers.update", "compute.routers.delete", "cloudscheduler.jobs.pause", "cloudscheduler.jobs.enable", "compute.networks.get", "compute.networks.delete", "compute.subnetworks.get", "compute.subnetworks.delete", "compute.firewalls.get", "compute.firewalls.delete"]
}
resource "google_project_iam_member" "watchdog" {
  project = var.project_id
  role    = google_project_iam_custom_role.watchdog.name
  member  = "serviceAccount:${google_service_account.runtime["watchdog"].email}"
}
resource "google_storage_bucket_iam_member" "evidence_writer" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
  bucket   = google_storage_bucket.evidence.name
  role     = each.key
  member   = "serviceAccount:${google_service_account.runtime["controller"].email}"
}
resource "google_storage_bucket_iam_member" "watchdog_inventory" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime["watchdog"].email}"
}
resource "google_artifact_registry_repository_iam_member" "reader" {
  for_each   = toset(["controller", "node"])
  location   = var.region
  repository = google_artifact_registry_repository.evaluation.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.runtime[each.key].email}"
}
resource "google_project_iam_member" "node" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.runtime["node"].email}"
}
resource "google_project_iam_member" "controller" {
  for_each = toset(["roles/container.developer", "roles/container.clusterViewer", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.runtime["controller"].email}"
}
# Controller may recreate only the cluster root and update the already-deployed
# watchdog revision/schedule; local operator owns VM/network final teardown.
resource "google_project_iam_member" "controller_infrastructure" {
  for_each = toset(["roles/container.clusterAdmin", "roles/compute.networkUser", "roles/compute.viewer"])
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.runtime["controller"].email}"
}
resource "google_project_iam_custom_role" "controller_watchdog" {
  role_id     = "karthosEvaluationWatchdogController"
  title       = "Karthos approved watchdog updates"
  permissions = ["run.services.get", "run.services.update", "run.operations.get", "cloudscheduler.jobs.get", "cloudscheduler.jobs.list", "cloudscheduler.jobs.pause", "cloudscheduler.jobs.enable"]
}
resource "google_project_iam_member" "controller_watchdog" {
  project = var.project_id
  role    = google_project_iam_custom_role.controller_watchdog.name
  member  = "serviceAccount:${google_service_account.runtime["controller"].email}"
}
resource "google_service_account_iam_member" "controller_attach" {
  for_each           = toset(["node", "watchdog"])
  service_account_id = google_service_account.runtime[each.key].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.runtime["controller"].email}"
}
resource "google_service_account_iam_member" "controller_test_oidc" {
  service_account_id = google_service_account.runtime["scheduler"].name
  role               = "roles/iam.serviceAccountOpenIdTokenCreator"
  member             = "serviceAccount:${google_service_account.runtime["controller"].email}"
}
resource "google_storage_bucket_iam_member" "controller_state" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime["controller"].email}"
}

resource "google_storage_bucket_iam_member" "watchdog_result_writer" {
  bucket = google_storage_bucket.evidence.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.runtime["watchdog"].email}"
}

# Watchdog can inspect/release only this lease object, never tfstate objects.
resource "google_project_iam_custom_role" "watchdog_lease" {
  role_id     = "karthosEvaluationLeaseFinalizer"
  title       = "Karthos owned project lease finalizer"
  permissions = ["storage.objects.get", "storage.objects.delete"]
}
resource "google_storage_bucket_iam_member" "watchdog_lease" {
  bucket = google_storage_bucket.state.name
  role   = google_project_iam_custom_role.watchdog_lease.name
  member = "serviceAccount:${google_service_account.runtime["watchdog"].email}"
  condition {
    title      = "OnlyActiveRunLease"
    expression = "resource.name == 'projects/_/buckets/${google_storage_bucket.state.name}/objects/control/active-run.json'"
  }
}

# Separate copy; same-project storage is not account-level disaster recovery.
resource "google_storage_bucket" "backup" {
  name                        = "${var.project_id}-karthos-eval-evidence-backup"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  retention_policy {
    retention_period = 7776000
    is_locked        = false
  }
  versioning {
    enabled = true
  }
  lifecycle {
    prevent_destroy = true
  }
}
resource "google_storage_bucket_iam_member" "backup_writer" {
  for_each = toset(["roles/storage.objectCreator", "roles/storage.objectViewer"])
  bucket   = google_storage_bucket.backup.name
  role     = each.key
  member   = "serviceAccount:${google_service_account.runtime["controller"].email}"
}
resource "google_service_account_iam_member" "operator_test_oidc" {
  count              = var.operator_member == "" ? 0 : 1
  service_account_id = google_service_account.runtime["scheduler"].name
  role               = "roles/iam.serviceAccountOpenIdTokenCreator"
  member             = var.operator_member
}

# Restricted originals are separate from normal, redacted research evidence.
# Per-launch conditional IAM is reviewed separately; no permanent writer grant.
resource "google_storage_bucket" "quarantine" {
  name                        = "${var.project_id}-karthos-eval-quarantine"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  retention_policy {
    retention_period = 7776000
    is_locked        = false
  }
  versioning {
    enabled = true
  }
  lifecycle {
    prevent_destroy = true
  }
}
