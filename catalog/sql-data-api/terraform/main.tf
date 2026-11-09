data "azurerm_client_config" "current" {}

locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "sql-data-api"
  }, var.tags)

  sql_name = "${var.prefix}-${var.environment}-sql-${random_string.suffix.result}"
}

resource "random_string" "suffix" {
  length  = 8
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

resource "azurerm_mssql_server" "this" {
  name                          = local.sql_name
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = true
  tags                          = local.base_tags

  azuread_administrator {
    login_username              = var.sql_admin_login
    object_id                   = var.sql_admin_object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
    azuread_authentication_only = true
  }
}

resource "azurerm_mssql_database" "this" {
  name           = "${var.prefix}db"
  server_id      = azurerm_mssql_server.this.id
  sku_name       = var.sql_sku_name
  zone_redundant = var.sql_zone_redundant
  tags           = local.base_tags
}

resource "azurerm_mssql_firewall_rule" "allow_azure" {
  name             = "AllowAllWindowsAzureIps"
  server_id        = azurerm_mssql_server.this.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_service_plan" "this" {
  name                   = "${var.prefix}-${var.environment}-plan"
  resource_group_name    = azurerm_resource_group.this.name
  location               = azurerm_resource_group.this.location
  os_type                = "Linux"
  sku_name               = var.app_service_sku
  worker_count           = var.app_capacity
  zone_balancing_enabled = var.app_zone_redundant
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
    health_check_path   = "/health"

    health_check_eviction_time_in_min = 2

    application_stack {
      dotnet_version = "8.0"
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING      = azurerm_application_insights.this.connection_string
    ApplicationInsightsAgent_EXTENSION_VERSION = "~3"
    # Passwordless connection: the web app's managed identity authenticates to SQL.
    ConnectionStrings__Default = "Server=tcp:${azurerm_mssql_server.this.fully_qualified_domain_name},1433;Database=${azurerm_mssql_database.this.name};Authentication=Active Directory Default;Encrypt=True;"
  }
}
