output "sql_virtual_machine_name" {
  description = "Name of the SQL Server virtual machine."
  value       = azurerm_windows_virtual_machine.sql.name
}

output "sql_virtual_machine_id" {
  description = "Resource ID of the SQL Server virtual machine."
  value       = azurerm_windows_virtual_machine.sql.id
}

output "sql_virtual_machine_private_ip" {
  description = "Private IP address of the SQL Server virtual machine."
  value       = azurerm_network_interface.sql.private_ip_address
}

output "storage_account_name" {
  description = "Name of the private storage account."
  value       = azurerm_storage_account.this.name
}

output "storage_account_id" {
  description = "Resource ID of the private storage account."
  value       = azurerm_storage_account.this.id
}

output "blob_private_endpoint_id" {
  description = "Resource ID of the Blob private endpoint."
  value       = azurerm_private_endpoint.blob.id
}

output "virtual_network_id" {
  description = "Resource ID of the virtual network."
  value       = azurerm_virtual_network.this.id
}

output "bastion_id" {
  description = "Resource ID of Azure Bastion."
  value       = azurerm_bastion_host.this.id
}