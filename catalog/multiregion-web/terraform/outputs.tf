output "front_door_endpoint_host_name" {
  value = azurerm_cdn_frontdoor_endpoint.this.host_name
}

output "primary_app_host_name" {
  value = azurerm_linux_web_app.primary.default_hostname
}

output "secondary_app_host_name" {
  value = azurerm_linux_web_app.secondary.default_hostname
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.this.endpoint
}
