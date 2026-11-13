output "evidence_bucket" {
  value = google_storage_bucket.evidence.name
}
output "state_bucket" {
  value = google_storage_bucket.state.name
}
output "artifact_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.evaluation.repository_id}"
}
output "controller_service_account" {
  value = google_service_account.runtime["controller"].email
}
output "node_service_account" {
  value = google_service_account.runtime["node"].email
}
output "watchdog_service_account" {
  value = google_service_account.runtime["watchdog"].email
}
output "scheduler_service_account" {
  value = google_service_account.runtime["scheduler"].email
}
output "backup_bucket" {
  value = google_storage_bucket.backup.name
}

output "quarantine_bucket" {
  value = google_storage_bucket.quarantine.name
}
