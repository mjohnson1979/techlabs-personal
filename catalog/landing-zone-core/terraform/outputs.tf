output "vnet_name" {
  value       = azurerm_virtual_network.hub.name
  description = "Name of the hub virtual network."
}

output "nsg_name" {
  value       = azurerm_network_security_group.this.name
  description = "Name of the network security group."
}

output "key_vault_name" {
  value       = azurerm_key_vault.this.name
  description = "Name of the Key Vault."
}

output "storage_account_name" {
  value       = azurerm_storage_account.this.name
  description = "Name of the storage account."
}

output "workspace_name" {
  value       = azurerm_log_analytics_workspace.this.name
  description = "Name of the Log Analytics workspace."
}
