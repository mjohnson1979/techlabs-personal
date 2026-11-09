locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "integration-workflow"
    workload    = "integration-workflow"
  }, var.tags)

  storage_name = lower("${var.name_prefix}wf${random_string.suffix.result}")
  is_premium   = var.service_bus_sku == "Premium"
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

resource "azurerm_application_insights" "this" {
  name                = "${var.name_prefix}-ai"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  tags                = local.base_tags
}

resource "azurerm_storage_account" "this" {
  name                            = local.storage_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  tags                            = local.base_tags
}

resource "azurerm_servicebus_namespace" "this" {
  name                         = "${var.name_prefix}-sb"
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  sku                          = var.service_bus_sku
  capacity                     = local.is_premium ? 1 : 0
  premium_messaging_partitions = local.is_premium ? 1 : 0
  local_auth_enabled           = false
  minimum_tls_version          = "1.2"
  tags                         = local.base_tags
}

resource "azurerm_servicebus_queue" "inbound" {
  name                                 = "inbound"
  namespace_id                         = azurerm_servicebus_namespace.this.id
  max_delivery_count                   = 10
  dead_lettering_on_message_expiration = true
  lock_duration                        = "PT1M"
}

resource "azurerm_servicebus_queue" "outbound" {
  name                                 = "outbound"
  namespace_id                         = azurerm_servicebus_namespace.this.id
  max_delivery_count                   = 10
  dead_lettering_on_message_expiration = true
}

resource "azurerm_service_plan" "this" {
  name                = "${var.name_prefix}-plan"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  os_type             = "Windows"
  sku_name            = var.workflow_sku
  tags                = local.base_tags
}

resource "azurerm_logic_app_standard" "this" {
  name                       = "${var.name_prefix}-logic"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  app_service_plan_id        = azurerm_service_plan.this.id
  storage_account_name       = azurerm_storage_account.this.name
  storage_account_access_key = azurerm_storage_account.this.primary_access_key
  https_only                 = true
  version                    = "~4"
  tags                       = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    min_tls_version = "1.2"
    ftps_state      = "Disabled"
  }

  app_settings = {
    FUNCTIONS_WORKER_RUNTIME              = "node"
    APP_KIND                              = "workflowApp"
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string
    "serviceBus__fullyQualifiedNamespace" = "${azurerm_servicebus_namespace.this.name}.servicebus.windows.net"
  }
}

# Logic App identity can send/receive on Service Bus (keyless connector).
resource "azurerm_role_assignment" "sb_data_owner" {
  scope                = azurerm_servicebus_namespace.this.id
  role_definition_name = "Azure Service Bus Data Owner"
  principal_id         = azurerm_logic_app_standard.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}
