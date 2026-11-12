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
variable "state_bucket_name" {
  type = string
}

variable "operator_member" {
  type    = string
  default = ""
  validation {
    condition     = var.operator_member == "" || can(regex("^user:[^[:space:]]+@[^[:space:]]+$", var.operator_member))
    error_message = "Use an explicit operator user member or omit the optional OIDC test grant."
  }
}
