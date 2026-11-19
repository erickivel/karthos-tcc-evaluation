locals {
  labels = {
    evaluation-owner = var.owner_id, evaluation-run = var.run_id
  }
}
resource "google_compute_network" "evaluation" {
  description             = jsonencode(local.labels)
  name                    = local.resource_prefix
  auto_create_subnetworks = false
}
resource "google_compute_subnetwork" "evaluation" {
  description              = jsonencode(local.labels)
  name                     = local.resource_prefix
  region                   = var.region
  ip_cidr_range            = "10.40.0.0/20"
  network                  = google_compute_network.evaluation.id
  private_ip_google_access = true
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.44.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.48.0.0/20"
  }
}
resource "google_compute_router" "evaluation" {
  description = jsonencode(local.labels)
  name        = local.resource_prefix
  region      = var.region
  network     = google_compute_network.evaluation.id
}
resource "google_compute_router_nat" "evaluation" {
  name                               = local.resource_prefix
  router                             = google_compute_router.evaluation.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
resource "google_compute_firewall" "internal" {
  description   = jsonencode(local.labels)
  name          = "${local.resource_prefix}-internal"
  network       = google_compute_network.evaluation.id
  source_ranges = ["10.40.0.0/20", "10.44.0.0/16", "172.16.0.0/28"]
  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }
}
resource "google_compute_instance" "controller" {
  name         = "${local.resource_prefix}-controller"
  zone         = var.zone
  machine_type = "e2-standard-4"
  labels       = local.labels
  boot_disk {
    initialize_params {
      image = var.controller_boot_image
      size  = 300
      type  = "pd-balanced"
    }
  }
  network_interface {
    subnetwork = google_compute_subnetwork.evaluation.id
  }
  service_account {
    email  = var.controller_service_account
    scopes = ["cloud-platform", "userinfo-email"]
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  metadata = {
    runtime-enabled              = tostring(var.runtime_enabled)
    bootstrap-context-uri        = var.bootstrap_context_uri
    bootstrap-context-generation = var.bootstrap_context_generation
    enable-oslogin               = "TRUE"
    block-project-ssh-keys       = "TRUE"
    bootstrap-sdk-image          = var.bootstrap_sdk_image
    controller-image             = var.controller_image
    manifest-uri                 = var.manifest_uri
    manifest-generation          = var.manifest_generation
    manifest-sha256              = var.manifest_sha256
  }
  metadata_startup_script = file("${path.module}/../../controller/startup.sh")
  depends_on              = [google_compute_router_nat.evaluation]
}
resource "google_cloud_run_v2_service" "watchdog" {
  name                = "${local.resource_prefix}-watchdog"
  location            = var.region
  deletion_protection = false
  # Internet ingress is IAM protected; no allUsers/allAuthenticatedUsers binding.
  ingress = "INGRESS_TRAFFIC_ALL"
  template {
    service_account = var.watchdog_service_account
    timeout         = "300s"
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image   = var.watchdog_image
      command = ["functions-framework"]
      args    = ["--target=handler", "--source=/opt/watchdog/main.py", "--port=8080"]
      env {
        name  = "SCHEDULER_NAME"
        value = "projects/${var.project_id}/locations/${var.region}/jobs/${local.resource_prefix}-watchdog"
      }
      env {
        name  = "STATE_BUCKET"
        value = var.state_bucket
      }
      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "INVENTORY_BUCKET"
        value = var.evidence_bucket
      }
      env {
        name  = "INVENTORY_OBJECT"
        value = var.inventory_object
      }
      env {
        name  = "INVENTORY_GENERATION"
        value = var.inventory_generation
      }
      env {
        name  = "DRY_RUN"
        value = "false"
      }
    }
  }
}
resource "google_cloud_run_v2_service_iam_member" "scheduler" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.watchdog.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.scheduler_service_account}"
}
resource "google_cloud_scheduler_job" "watchdog" {
  name      = "${local.resource_prefix}-watchdog"
  region    = var.region
  schedule  = "*/5 * * * *"
  time_zone = "Etc/UTC"
  paused    = !var.watchdog_enabled
  http_target {
    http_method = "POST"
    uri         = google_cloud_run_v2_service.watchdog.uri
    oidc_token {
      service_account_email = var.scheduler_service_account
      audience              = google_cloud_run_v2_service.watchdog.uri
    }
  }
}
