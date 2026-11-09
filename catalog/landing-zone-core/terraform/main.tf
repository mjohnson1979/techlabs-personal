data "azurerm_client_config" "current" {}

locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "landing-zone-core"
  }, var.tags)

  storage_replication = replace(var.storage_sku, "Standard_", "")
}

resource "random_string" "suffix" {
  length  = 16
  upper   = false
  special = false
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.base_tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.prefix}-law"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.base_tags
}

resource "azurerm_storage_account" "this" {
  name                            = "${random_string.suffix.result}stg"
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

resource "azurerm_key_vault" "this" {
  name                       = "${var.prefix}-kv"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  tags                       = local.base_tags
}

resource "azurerm_virtual_network" "hub" {
  name                = "${var.prefix}-hub-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = ["10.0.0.0/16"]
  tags                = local.base_tags
}

resource "azurerm_subnet" "default" {
  name                 = "default"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.hub.name
  address_prefixes     = ["10.0.1.0/24"]
}

resource "azurerm_network_security_group" "this" {
  name                = "${var.prefix}-nsg"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags
}
