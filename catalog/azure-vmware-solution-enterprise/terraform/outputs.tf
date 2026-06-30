output "private_cloud_id" {
  value       = azurerm_vmware_private_cloud.this.id
  description = "Resource ID of the AVS private cloud."
}

output "vnet_id" {
  value       = azurerm_virtual_network.this.id
  description = "Resource ID of the landing virtual network."
}

output "key_vault_id" {
  value       = azurerm_key_vault.this.id
  description = "Resource ID of the Key Vault baseline."
}

output "express_route_gateway_id" {
  value       = var.deploy_connectivity ? azurerm_virtual_network_gateway.express_route[0].id : ""
  description = "Resource ID of the ExpressRoute gateway, or empty when connectivity is not deployed."
}

output "log_analytics_workspace_id" {
  value       = azurerm_log_analytics_workspace.this.id
  description = "Resource ID of the Log Analytics workspace."
}
