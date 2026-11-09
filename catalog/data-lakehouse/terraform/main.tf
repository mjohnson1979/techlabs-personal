data "azurerm_client_config" "current" {}

locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "data-lakehouse"
    workload    = "data-lakehouse"
  }, var.tags)

  lake_name             = lower("${var.name_prefix}lake${random_string.suffix.result}")
  lake_replication      = replace(var.lake_storage_sku, "Standard_", "")
  managed_rg_databricks = "${var.name_prefix}-dbw-managed"
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
  numeric = true
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.base_tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name_prefix}-law"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days
  tags                = local.base_tags
}

resource "azurerm_key_vault" "this" {
  name                       = "${var.name_prefix}-kv-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  tags                       = local.base_tags
}

resource "azurerm_storage_account" "lake" {
  name                            = local.lake_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = local.lake_replication
  account_kind                    = "StorageV2"
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  tags                            = local.base_tags

  network_rules {
    default_action = "Allow"
    bypass         = ["AzureServices"]
  }
}

resource "azurerm_storage_container" "bronze" {
  name                  = "bronze"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "silver" {
  name                  = "silver"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "gold" {
  name                  = "gold"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_data_factory" "this" {
  name                            = "${var.name_prefix}-adf"
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  managed_virtual_network_enabled = true
  tags                            = local.base_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_data_factory_integration_runtime_azure" "auto_resolve" {
  name                    = "AutoResolveIntegrationRuntime"
  data_factory_id         = azurerm_data_factory.this.id
  location                = "AutoResolve"
  virtual_network_enabled = true
}

resource "azurerm_databricks_workspace" "this" {
  name                        = "${var.name_prefix}-dbw"
  resource_group_name         = azurerm_resource_group.this.name
  location                    = azurerm_resource_group.this.location
  sku                         = var.databricks_sku
  managed_resource_group_name = local.managed_rg_databricks
  tags                        = local.base_tags

  custom_parameters {
    no_public_ip = var.databricks_no_public_ip
  }
}

resource "azurerm_monitor_diagnostic_setting" "adf" {
  name                       = "adf-diagnostics"
  target_resource_id         = azurerm_data_factory.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}

# Data Factory can read/write the data lake.
resource "azurerm_role_assignment" "adf_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_data_factory.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}
