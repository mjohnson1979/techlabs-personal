locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "enterprise-rag"
    workload    = "enterprise-rag"
  }, var.tags)

  documents_name         = lower("${var.name_prefix}docs")
  cosmos_name            = lower("${var.name_prefix}-cosmos")
  cosmos_data_contrib_id = "00000000-0000-0000-0000-000000000002"
  search_endpoint        = "https://${var.name_prefix}-search.search.windows.net"
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
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.this.id
  tags                = local.base_tags
}

resource "azurerm_storage_account" "documents" {
  name                            = local.documents_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = var.zone_redundant ? "ZRS" : "LRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  tags                            = local.base_tags
}

resource "azurerm_cognitive_account" "openai" {
  name                          = "${var.name_prefix}-openai"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = "${var.name_prefix}-openai"
  local_auth_enabled            = false
  public_network_access_enabled = true
  tags                          = local.base_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.chat_model
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.chat_model
    version = var.chat_model_version
  }

  sku {
    name     = var.chat_model_sku
    capacity = var.chat_capacity
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.embedding_model
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.embedding_model
    version = var.embedding_model_version
  }

  sku {
    name     = "Standard"
    capacity = 50
  }

  depends_on = [azurerm_cognitive_deployment.chat]
}

resource "azurerm_search_service" "this" {
  name                          = "${var.name_prefix}-search"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  sku                           = var.search_sku
  replica_count                 = var.search_replicas
  partition_count               = 1
  local_authentication_enabled  = false
  semantic_search_sku           = "standard"
  public_network_access_enabled = true
  tags                          = local.base_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_cosmosdb_account" "this" {
  name                         = local.cosmos_name
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  offer_type                   = "Standard"
  kind                         = "GlobalDocumentDB"
  local_authentication_enabled = false
  automatic_failover_enabled   = var.zone_redundant
  minimal_tls_version          = "Tls12"
  tags                         = local.base_tags

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.this.location
    failover_priority = 0
    zone_redundant    = var.zone_redundant
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

resource "azurerm_cosmosdb_sql_container" "history" {
  name                = "history"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  database_name       = azurerm_cosmosdb_sql_database.chat.name
  partition_key_paths = ["/sessionId"]
}

resource "azurerm_service_plan" "this" {
  name                   = "${var.name_prefix}-plan"
  resource_group_name    = azurerm_resource_group.this.name
  location               = azurerm_resource_group.this.location
  os_type                = "Linux"
  sku_name               = var.app_service_sku
  worker_count           = var.zone_redundant ? 3 : 1
  zone_balancing_enabled = var.zone_redundant
  tags                   = local.base_tags
}

resource "azurerm_linux_web_app" "this" {
  name                = "${var.name_prefix}-app"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  service_plan_id     = azurerm_service_plan.this.id
  https_only          = true
  tags                = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    always_on                         = true
    http2_enabled                     = true
    minimum_tls_version               = "1.2"
    ftps_state                        = "Disabled"
    health_check_path                 = "/health"
    health_check_eviction_time_in_min = 2

    application_stack {
      python_version = "3.12"
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string
    AZURE_OPENAI_ENDPOINT                 = azurerm_cognitive_account.openai.endpoint
    AZURE_OPENAI_CHAT_DEPLOYMENT          = var.chat_model
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT     = var.embedding_model
    AZURE_SEARCH_ENDPOINT                 = local.search_endpoint
    AZURE_COSMOS_ENDPOINT                 = azurerm_cosmosdb_account.this.endpoint
    AZURE_STORAGE_ACCOUNT                 = azurerm_storage_account.documents.name
  }
}

resource "azurerm_api_management" "this" {
  name                = "${var.name_prefix}-apim"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  publisher_name      = var.apim_publisher_name
  publisher_email     = var.apim_publisher_email
  sku_name            = "${var.apim_sku}_1"
  tags                = local.base_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_api_management_api" "rag" {
  name                  = "rag-api"
  api_management_name   = azurerm_api_management.this.name
  resource_group_name   = azurerm_resource_group.this.name
  revision              = "1"
  display_name          = "RAG Chat API"
  path                  = "chat"
  protocols             = ["https"]
  service_url           = "https://${azurerm_linux_web_app.this.default_hostname}"
  subscription_required = true
}

# App identity can call Azure OpenAI.
resource "azurerm_role_assignment" "app_openai" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_linux_web_app.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Search identity can call Azure OpenAI for integrated vectorization.
resource "azurerm_role_assignment" "search_openai" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_search_service.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# App identity can query the search index.
resource "azurerm_role_assignment" "app_search_reader" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Reader"
  principal_id         = azurerm_linux_web_app.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# App identity can manage indexes / indexers.
resource "azurerm_role_assignment" "app_search_contributor" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Service Contributor"
  principal_id         = azurerm_linux_web_app.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Search identity can read source documents from blob storage.
resource "azurerm_role_assignment" "search_storage" {
  scope                = azurerm_storage_account.documents.id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_search_service.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# App identity can read/write Cosmos data plane.
resource "azurerm_cosmosdb_sql_role_assignment" "app" {
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/${local.cosmos_data_contrib_id}"
  principal_id        = azurerm_linux_web_app.this.identity[0].principal_id
  scope               = azurerm_cosmosdb_account.this.id
}
