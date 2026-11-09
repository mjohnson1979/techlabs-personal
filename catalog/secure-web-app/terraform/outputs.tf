output "web_app_host_name" {
  value       = azurerm_linux_web_app.this.default_hostname
  description = "Default hostname of the App Service (private)."
}

output "app_gateway_public_ip" {
  value       = azurerm_public_ip.app_gateway.ip_address
  description = "Public IP of the Application Gateway."
}

output "sql_server_fqdn" {
  value       = azurerm_mssql_server.this.fully_qualified_domain_name
  description = "Fully qualified domain name of the SQL server."
}

output "key_vault_uri" {
  value       = azurerm_key_vault.this.vault_uri
  description = "URI of the Key Vault."
}
