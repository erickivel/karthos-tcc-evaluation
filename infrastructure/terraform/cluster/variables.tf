variable "project_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Invalid project_id; use an explicit approved value."
  }
}
variable "region" {
  type    = string
  default = "us-central1"
  validation {
    condition     = var.region == "us-central1"
    error_message = "Invalid region; use an explicit approved value."
  }
}
variable "zone" {
  type    = string
  default = "us-central1-a"
  validation {
    condition     = contains(["us-central1-a", "us-central1-b"], var.zone)
    error_message = "Invalid zone; use an explicit approved value."
  }
}
variable "owner_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,47}$", var.owner_id))
    error_message = "Use a lowercase letter followed by lowercase letters, digits or hyphens; maximum 48 characters."
  }
}
variable "run_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,47}$", var.run_id))
    error_message = "Use a lowercase letter followed by lowercase letters, digits or hyphens; maximum 48 characters."
  }
}
variable "network" {
  type = string
}
variable "subnetwork" {
  type = string
}
variable "node_service_account" {
  type = string
}
variable "kubernetes_version" {
  type = string
}

variable "maintenance_start_time" {
  type = string
}
variable "maintenance_end_time" {
  type = string
}
