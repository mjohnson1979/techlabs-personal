output "workspace_name" {
  value       = azurerm_log_analytics_workspace.this.name
  description = "Name of the Log Analytics workspace."
}

output "key_vault_name" {
  value       = azurerm_key_vault.this.name
  description = "Name of the Key Vault."
}

output "storage_account_name" {
  value       = azurerm_storage_account.this.name
  description = "Name of the storage account."
}

output "ai_services_name" {
  value       = azurerm_cognitive_account.ai.name
  description = "Name of the AI Services (AI Foundry) account."
}

output "ai_services_endpoint" {
  value       = azurerm_cognitive_account.ai.endpoint
  description = "Endpoint of the AI Services account."
}

output "search_name" {
  value       = azurerm_search_service.this.name
  description = "Name of the AI Search service."
}

output "search_endpoint" {
  value       = "https://${azurerm_search_service.this.name}.search.windows.net"
  description = "Endpoint of the AI Search service."
}
