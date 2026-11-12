locals {
  # 33 characters, independent of caller identifier lengths; labels retain full identity.
  cluster_name = "karthos-eval-${substr(sha256(jsonencode([var.owner_id, var.run_id])), 0, 20)}"
}
