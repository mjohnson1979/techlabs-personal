output "lake_account_name" {
  value = azurerm_storage_account.lake.name
}

output "data_factory_name" {
  value = azurerm_data_factory.this.name
}

output "databricks_workspace_url" {
  value = azurerm_databricks_workspace.this.workspace_url
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}
