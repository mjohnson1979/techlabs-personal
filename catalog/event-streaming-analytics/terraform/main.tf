locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "event-streaming-analytics"
    workload    = "event-streaming-analytics"
  }, var.tags)

  lake_name              = lower("${var.name_prefix}lake")
  func_storage_name      = lower("${var.name_prefix}func")
  cosmos_name            = lower("${var.name_prefix}-cosmos")
  is_standard_ehns       = var.event_hubs_sku == "Standard"
  cosmos_data_contrib_id = "00000000-0000-0000-0000-000000000002"
  event_hub_namespace    = "${var.name_prefix}-ehns"
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

resource "azurerm_storage_account" "lake" {
  name                            = local.lake_name
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

resource "azurerm_storage_container" "curated" {
  name                  = "curated"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

resource "azurerm_storage_account" "func" {
  name                            = local.func_storage_name
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

resource "azurerm_eventhub_namespace" "this" {
  name                         = local.event_hub_namespace
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  sku                          = var.event_hubs_sku
  capacity                     = var.throughput_units
  local_authentication_enabled = false
  minimum_tls_version          = "1.2"
  auto_inflate_enabled         = local.is_standard_ehns
  maximum_throughput_units     = local.is_standard_ehns ? 10 : null
  tags                         = local.base_tags
}

resource "azurerm_eventhub" "telemetry" {
  name                = "telemetry"
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = azurerm_resource_group.this.name
  partition_count     = var.partition_count
  message_retention   = var.message_retention_days
}

resource "azurerm_eventhub_consumer_group" "analytics" {
  name                = "analytics"
  eventhub_name       = azurerm_eventhub.telemetry.name
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_eventhub_consumer_group" "functions" {
  name                = "functions"
  eventhub_name       = azurerm_eventhub.telemetry.name
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = azurerm_resource_group.this.name
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
    zone_redundant    = var.zone_redundant
  }

  capabilities {
    name = "EnableServerless"
  }
}

resource "azurerm_cosmosdb_sql_database" "telemetry" {
  name                = "telemetry"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "events" {
  name                = "events"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  database_name       = azurerm_cosmosdb_sql_database.telemetry.name
  partition_key_paths = ["/deviceId"]
}

resource "azurerm_stream_analytics_job" "this" {
  name                                     = "${var.name_prefix}-asa"
  resource_group_name                      = azurerm_resource_group.this.name
  location                                 = azurerm_resource_group.this.location
  sku_name                                 = "Standard"
  streaming_units                          = var.streaming_units
  compatibility_level                      = "1.2"
  data_locale                              = "en-US"
  events_late_arrival_max_delay_in_seconds = 16
  events_out_of_order_max_delay_in_seconds = 5
  events_out_of_order_policy               = "Adjust"
  output_error_policy                      = "Stop"
  tags                                     = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  transformation_query = "SELECT deviceId, eventType, value, System.Timestamp() AS windowEnd INTO [lake-output] FROM [eventhub-input] TIMESTAMP BY eventTime"
}

resource "azurerm_stream_analytics_stream_input_eventhub" "input" {
  name                         = "eventhub-input"
  stream_analytics_job_name    = azurerm_stream_analytics_job.this.name
  resource_group_name          = azurerm_resource_group.this.name
  servicebus_namespace         = azurerm_eventhub_namespace.this.name
  eventhub_name                = azurerm_eventhub.telemetry.name
  eventhub_consumer_group_name = azurerm_eventhub_consumer_group.analytics.name
  authentication_mode          = "Msi"

  serialization {
    type     = "Json"
    encoding = "UTF8"
  }
}

resource "azurerm_stream_analytics_output_blob" "lake" {
  name                      = "lake-output"
  stream_analytics_job_name = azurerm_stream_analytics_job.this.name
  resource_group_name       = azurerm_resource_group.this.name
  storage_account_name      = azurerm_storage_account.lake.name
  storage_container_name    = azurerm_storage_container.curated.name
  path_pattern              = "telemetry/{date}/{time}"
  date_format               = "yyyy/MM/dd"
  time_format               = "HH"
  authentication_mode       = "Msi"

  serialization {
    type = "Parquet"
  }
}

resource "azurerm_service_plan" "function" {
  name                         = "${var.name_prefix}-func-plan"
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  os_type                      = "Linux"
  sku_name                     = var.function_plan_sku
  maximum_elastic_worker_count = var.function_plan_sku == "Y1" ? null : 10
  tags                         = local.base_tags
}

resource "azurerm_linux_function_app" "this" {
  name                          = "${var.name_prefix}-func"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  service_plan_id               = azurerm_service_plan.function.id
  storage_account_name          = azurerm_storage_account.func.name
  storage_uses_managed_identity = true
  https_only                    = true
  functions_extension_version   = "~4"
  tags                          = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  site_config {
    minimum_tls_version = "1.2"
    ftps_state          = "Disabled"

    application_stack {
      dotnet_version              = "8.0"
      use_dotnet_isolated_runtime = true
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING       = azurerm_application_insights.this.connection_string
    EventHubConnection__fullyQualifiedNamespace = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net"
    EventHubName                                = azurerm_eventhub.telemetry.name
    CosmosEndpoint                              = azurerm_cosmosdb_account.this.endpoint
  }
}

# Stream Analytics can receive from Event Hubs.
resource "azurerm_role_assignment" "asa_eventhub" {
  scope                = azurerm_eventhub_namespace.this.id
  role_definition_name = "Azure Event Hubs Data Receiver"
  principal_id         = azurerm_stream_analytics_job.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Stream Analytics can write curated parquet to the lake.
resource "azurerm_role_assignment" "asa_storage" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_stream_analytics_job.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Function can receive from Event Hubs.
resource "azurerm_role_assignment" "func_eventhub" {
  scope                = azurerm_eventhub_namespace.this.id
  role_definition_name = "Azure Event Hubs Data Receiver"
  principal_id         = azurerm_linux_function_app.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Function host storage access.
resource "azurerm_role_assignment" "func_storage" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_linux_function_app.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

# Function can write to the Cosmos serving store.
resource "azurerm_cosmosdb_sql_role_assignment" "func" {
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/${local.cosmos_data_contrib_id}"
  principal_id        = azurerm_linux_function_app.this.identity[0].principal_id
  scope               = azurerm_cosmosdb_account.this.id
}
