locals {
  # Stable across cluster run/rebuild IDs; max 40 including longest resource suffix.
  resource_prefix = "karthos-eval-${substr(sha256(var.owner_id), 0, 16)}"
}
