output "logic_app_name" {
  value = azurerm_logic_app_standard.this.name
}

output "logic_app_hostname" {
  value = azurerm_logic_app_standard.this.default_hostname
}

output "service_bus_namespace" {
  value = "${azurerm_servicebus_namespace.this.name}.servicebus.windows.net"
}
