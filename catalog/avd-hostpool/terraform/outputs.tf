output "host_pool_name" {
  value = azurerm_virtual_desktop_host_pool.this.name
}

output "application_group_name" {
  value = azurerm_virtual_desktop_application_group.this.name
}

output "workspace_name" {
  value = azurerm_virtual_desktop_workspace.this.name
}
