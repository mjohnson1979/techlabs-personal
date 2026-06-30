output "api_fqdn" {
  value = azurerm_container_app.api.ingress[0].fqdn
}

output "environment_name" {
  value = azurerm_container_app_environment.this.name
}

output "service_bus_namespace" {
  value = "${azurerm_servicebus_namespace.this.name}.servicebus.windows.net"
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.this.endpoint
}
