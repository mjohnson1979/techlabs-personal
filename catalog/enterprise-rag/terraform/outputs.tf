output "orchestrator_host_name" {
  value = azurerm_linux_web_app.this.default_hostname
}

output "apim_gateway_url" {
  value = azurerm_api_management.this.gateway_url
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "search_endpoint" {
  value = local.search_endpoint
}
