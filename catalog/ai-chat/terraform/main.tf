locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "ai-chat"
  }, var.tags)

  openai_name = "${var.prefix}-${var.environment}-openai-${random_string.suffix.result}"
  cosmos_name = "${var.prefix}-${var.environment}-cosmos-${random_string.suffix.result}"
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

resource "azurerm_cognitive_account" "openai" {
  name                          = local.openai_name
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = local.openai_name
  public_network_access_enabled = true
  local_auth_enabled            = false
  tags                          = local.base_tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.chat_model_name
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.chat_model_name
    version = var.chat_model_version
  }

  sku {
    name     = var.chat_model_sku
    capacity = var.chat_model_capacity
  }
}

resource "azurerm_cosmosdb_account" "this" {
  name                         = local.cosmos_name
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  offer_type                   = "Standard"
  kind                         = "GlobalDocumentDB"
  local_authentication_enabled = false
  minimal_tls_version          = "Tls12"
  tags                         = local.base_tags

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.this.location
    failover_priority = 0
    zone_redundant    = false
  }

  capabilities {
    name = "EnableServerless"
  }
}

resource "azurerm_cosmosdb_sql_database" "chat" {
  name                = "chat"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "conversations" {
  name                = "conversations"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  database_name       = azurerm_cosmosdb_sql_database.chat.name
  partition_key_paths = ["/sessionId"]
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
    AZURE_OPENAI_ENDPOINT                      = azurerm_cognitive_account.openai.endpoint
    AZURE_OPENAI_DEPLOYMENT                    = azurerm_cognitive_deployment.chat.name
    COSMOS_ENDPOINT                            = azurerm_cosmosdb_account.this.endpoint
    COSMOS_DATABASE                            = azurerm_cosmosdb_sql_database.chat.name
    COSMOS_CONTAINER                           = azurerm_cosmosdb_sql_container.conversations.name
  }
}
