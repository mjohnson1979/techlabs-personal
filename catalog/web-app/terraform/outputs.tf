output "web_app_name" {
  value       = azurerm_linux_web_app.this.name
  description = "Name of the web app."
}

output "web_app_hostname" {
  value       = azurerm_linux_web_app.this.default_hostname
  description = "Default hostname of the web app."
}

output "web_app_principal_id" {
  value       = azurerm_linux_web_app.this.identity[0].principal_id
  description = "System-assigned managed identity principal ID."
}

output "app_insights_name" {
  value       = azurerm_application_insights.this.name
  description = "Application Insights component name."
}

output "workspace_name" {
  value       = azurerm_log_analytics_workspace.this.name
  description = "Log Analytics workspace name."
}
