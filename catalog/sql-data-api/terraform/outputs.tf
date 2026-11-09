output "web_app_name" {
  value = azurerm_linux_web_app.this.name
}

output "web_app_hostname" {
  value = azurerm_linux_web_app.this.default_hostname
}

output "web_app_principal_id" {
  value = azurerm_linux_web_app.this.identity[0].principal_id
}

output "sql_server_name" {
  value = azurerm_mssql_server.this.name
}

output "sql_database_name" {
  value = azurerm_mssql_database.this.name
}

output "workspace_name" {
  value = azurerm_log_analytics_workspace.this.name
}
