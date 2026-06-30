output "host_pool_id" {
  value       = azurerm_virtual_desktop_host_pool.this.id
  description = "Resource ID of the AVD host pool."
}

output "workspace_id" {
  value       = azurerm_virtual_desktop_workspace.this.id
  description = "Resource ID of the AVD workspace."
}

output "vnet_id" {
  value       = azurerm_virtual_network.this.id
  description = "Resource ID of the landing virtual network."
}

output "key_vault_id" {
  value       = azurerm_key_vault.this.id
  description = "Resource ID of the Key Vault baseline."
}

output "fslogix_storage_account_id" {
  value       = azurerm_storage_account.fslogix.id
  description = "Resource ID of the FSLogix profile storage account."
}

output "log_analytics_workspace_id" {
  value       = azurerm_log_analytics_workspace.this.id
  description = "Resource ID of the Log Analytics workspace."
}

output "session_host_count_deployed" {
  value       = local.session_host_count
  description = "Number of session host VMs deployed (0 when not deployed)."
}
