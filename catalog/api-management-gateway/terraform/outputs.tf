output "apim_gateway_url" {
  value = azurerm_api_management.this.gateway_url
}

output "apim_developer_portal_url" {
  value = azurerm_api_management.this.developer_portal_url
}

output "backend_host_name" {
  value = azurerm_linux_web_app.backend.default_hostname
}
