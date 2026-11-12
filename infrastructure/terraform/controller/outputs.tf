output "network" {
  value = google_compute_network.evaluation.id
}
output "subnetwork" {
  value = google_compute_subnetwork.evaluation.id
}
output "controller_name" {
  value = google_compute_instance.controller.name
}
output "controller_uid" {
  value = google_compute_instance.controller.instance_id
}
output "watchdog_uri" {
  value = google_cloud_run_v2_service.watchdog.uri
}

output "router_resource_name" { value = "projects/${var.project_id}/regions/${var.region}/routers/${google_compute_router.evaluation.name}" }

output "watchdog_service_name" { value = google_cloud_run_v2_service.watchdog.name }
output "scheduler_name" { value = google_cloud_scheduler_job.watchdog.id }

output "network_resource_name" { value = google_compute_network.evaluation.id }
output "subnetwork_resource_name" { value = google_compute_subnetwork.evaluation.id }
output "firewall_resource_name" { value = google_compute_firewall.internal.id }
