output "hub_vnet_id" {
  value = azurerm_virtual_network.hub.id
}

output "spoke_vnet_id" {
  value = azurerm_virtual_network.spoke.id
}

output "firewall_private_ip" {
  value = azurerm_firewall.this.ip_configuration[0].private_ip_address
}

output "workload_subnet_id" {
  value = azurerm_subnet.workload.id
}
