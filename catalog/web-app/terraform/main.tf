locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "web-app"
  }, var.tags)
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.base_tags
}

resource "random_string" "suffix" {
  length  = 8
  upper   = false
  special = false
  numeric = true
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.prefix}-${var.environment}-law"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.base_tags
}

resource "azurerm_application_insights" "this" {
  name                = "${var.prefix}-${var.environment}-ai"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "web"
  tags                = local.base_tags
}

resource "azurerm_service_plan" "this" {
  name                   = "${var.prefix}-${var.environment}-plan"
  resource_group_name    = azurerm_resource_group.this.name
  location               = azurerm_resource_group.this.location
  os_type                = "Linux"
  sku_name               = var.sku
  worker_count           = var.capacity
  zone_balancing_enabled = var.zone_redundant
  tags                   = local.base_tags
}

resource "azurerm_linux_web_app" "this" {
  name                = "${var.prefix}-${var.environment}-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  service_plan_id     = azurerm_service_plan.this.id
  https_only          = true
  tags                = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    minimum_tls_version = "1.2"
    ftps_state          = "Disabled"
    http2_enabled       = true
    health_check_path   = var.health_check_path

    application_stack {
      dotnet_version = var.dotnet_version
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING      = azurerm_application_insights.this.connection_string
    ApplicationInsightsAgent_EXTENSION_VERSION = "~3"
  }
}
