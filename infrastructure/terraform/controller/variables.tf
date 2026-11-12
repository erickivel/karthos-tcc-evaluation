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
variable "evidence_bucket" {
  type = string
}
variable "controller_service_account" {
  type = string
}
variable "watchdog_service_account" {
  type = string
}
variable "scheduler_service_account" {
  type = string
}
variable "manifest_uri" {
  type = string
}
variable "inventory_object" {
  type = string
}
variable "inventory_generation" {
  type = string
}
variable "manifest_generation" {
  type = string
}
variable "controller_boot_image" {
  type = string
  validation {
    condition     = can(regex("^projects/[a-z0-9-]+/global/images/[a-z0-9-]+$", var.controller_boot_image)) && !strcontains(var.controller_boot_image, "/family/")
    error_message = "Use an explicit reviewed immutable Compute image name, never an image family."
  }
}
variable "controller_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.controller_image))
    error_message = "Invalid controller_image; use an explicit approved value."
  }
}
variable "watchdog_image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.watchdog_image))
    error_message = "Invalid watchdog_image; use an explicit approved value."
  }
}
variable "manifest_sha256" {
  type = string
  validation {
    condition     = can(regex("^[a-f0-9]{64}$", var.manifest_sha256))
    error_message = "Invalid manifest_sha256; use an explicit approved value."
  }
}
variable "watchdog_enabled" {
  type    = bool
  default = false
}

variable "runtime_enabled" {
  type    = bool
  default = false
}
variable "bootstrap_context_uri" {
  type    = string
  default = ""
}
variable "bootstrap_context_generation" {
  type    = string
  default = ""
}

variable "state_bucket" {
  type = string
}

variable "bootstrap_sdk_image" {
  type    = string
  default = ""
  validation {
    condition     = var.bootstrap_sdk_image == "" || can(regex("@sha256:[a-f0-9]{64}$", var.bootstrap_sdk_image))
    error_message = "Use an immutable public SDK container image. Empty values fail closed at VM startup."
  }
}
