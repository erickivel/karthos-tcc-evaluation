resource "google_container_cluster" "evaluation" {
  name                     = local.cluster_name
  datapath_provider        = "LEGACY_DATAPATH"
  location                 = var.zone
  min_master_version       = var.kubernetes_version
  network                  = var.network
  subnetwork               = var.subnetwork
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false
  # Explicit Calico enforcement; Dataplane V2 restricts custom eBPF programs.
  network_policy {
    enabled  = true
    provider = "CALICO"
  }
  addons_config {
    network_policy_config {
      disabled = false
    }
  }
  resource_labels = {
    evaluation-owner = var.owner_id, evaluation-run = var.run_id
  }
  release_channel {
    channel = "STABLE"
  }
  maintenance_policy {
    # Required by provider 7.29.0 even when a study-wide exclusion is present.
    daily_maintenance_window {
      start_time = "03:00"
    }
    maintenance_exclusion {
      exclusion_name = "qualification-study"
      start_time     = var.maintenance_start_time
      end_time       = var.maintenance_end_time
      exclusion_options {
        scope = "NO_UPGRADES"
      }
    }
  }
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = true
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }
  master_authorized_networks_config {
    cidr_blocks {
      cidr_block   = "10.40.0.0/20"
      display_name = "controller-subnet"
    }
  }
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
  logging_config {
    enable_components = ["SYSTEM_COMPONENTS"]
  }
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
    managed_prometheus {
      enabled = false
    }
  }
}
resource "google_container_node_pool" "slot" {
  for_each   = toset(["observability", "app-a", "app-b"])
  name       = each.key
  location   = var.zone
  cluster    = google_container_cluster.evaluation.name
  version    = var.kubernetes_version
  node_count = 1
  management {
    auto_repair  = true
    auto_upgrade = true
  }
  node_config {
    machine_type    = "e2-standard-4"
    image_type      = "UBUNTU_CONTAINERD"
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    spot            = false
    preemptible     = false
    service_account = var.node_service_account
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels = {
      "evaluation.karthos.dev/role" = each.key == "observability" ? "observability" : "application"
      "evaluation.karthos.dev/slot" = each.key
    }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }
}
