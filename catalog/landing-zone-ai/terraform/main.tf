data "azurerm_client_config" "current" {}

locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "ai-landing-zone"
  }, var.tags)

  storage_replication = replace(var.storage_sku, "Standard_", "")
  ai_services_name    = "${var.prefix}-${var.environment}-aifoundry-${random_string.suffix.result}"
  search_name         = "${var.prefix}-${var.environment}-search-${random_string.suffix.result}"
}

resource "random_string" "suffix" {
  length  = 13
  upper   = false
  special = false
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.base_tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.prefix}-${var.environment}-ai-law"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.base_tags
}

resource "azurerm_key_vault" "this" {
  name                       = "${var.prefix}-${var.environment}-aikv"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  tags                       = local.base_tags
}

resource "azurerm_storage_account" "this" {
  name                            = "${random_string.suffix.result}aistg"
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = local.storage_replication
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  tags                            = local.base_tags
}

resource "azurerm_cognitive_account" "ai" {
  name                          = local.ai_services_name
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  kind                          = "AIServices"
  sku_name                      = "S0"
  custom_subdomain_name         = local.ai_services_name
  public_network_access_enabled = true
  local_auth_enabled            = false
  tags                          = local.base_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_search_service" "this" {
  name                          = local.search_name
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  sku                           = var.search_sku
  replica_count                 = var.search_replicas
  partition_count               = var.search_partitions
  local_authentication_enabled  = false
  public_network_access_enabled = true
  semantic_search_sku           = "free"
  tags                          = local.base_tags

  identity {
    type = "SystemAssigned"
  }
}
