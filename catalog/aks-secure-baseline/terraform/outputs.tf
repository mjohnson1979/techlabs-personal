output "aks_cluster_name" {
  value = azurerm_kubernetes_cluster.this.name
}

output "aks_oidc_issuer_url" {
  value = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "container_registry_login_server" {
  value = azurerm_container_registry.this.login_server
}

output "kubelet_identity_object_id" {
  value = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
}
