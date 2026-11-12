output "cluster_name" {
  value = google_container_cluster.evaluation.name
}
output "cluster_resource_name" {
  value = google_container_cluster.evaluation.id
}
output "cluster_endpoint" {
  value     = google_container_cluster.evaluation.endpoint
  sensitive = true
}
output "node_pools" {
  value = {
    for name, pool in google_container_node_pool.slot : name => pool.name
  }
}
