locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "microservices-aca"
    workload    = "microservices-aca"
  }, var.tags)

  is_premium             = var.service_bus_sku == "Premium"
  cosmos_name            = lower("${var.name_prefix}-cosmos")
  service_bus_namespace  = "${var.name_prefix}-sb"
  cosmos_data_contrib_id = "00000000-0000-0000-0000-000000000002"
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

resource "azurerm_user_assigned_identity" "app" {
  name                = "${var.name_prefix}-id"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags
}

resource "azurerm_servicebus_namespace" "this" {
  name                         = local.service_bus_namespace
  resource_group_name          = azurerm_resource_group.this.name
  location                     = azurerm_resource_group.this.location
  sku                          = var.service_bus_sku
  capacity                     = local.is_premium ? 1 : 0
  premium_messaging_partitions = local.is_premium ? 1 : 0
  local_auth_enabled           = false
  minimum_tls_version          = "1.2"
  tags                         = local.base_tags
}

resource "azurerm_servicebus_topic" "orders" {
  name                  = "orders"
  namespace_id          = azurerm_servicebus_namespace.this.id
  max_size_in_megabytes = 1024
}

resource "azurerm_servicebus_subscription" "worker" {
  name                                 = "worker"
  topic_id                             = azurerm_servicebus_topic.orders.id
  max_delivery_count                   = 10
  dead_lettering_on_message_expiration = true
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

resource "azurerm_cosmosdb_sql_database" "state" {
  name                = "state"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
}

resource "azurerm_cosmosdb_sql_container" "state" {
  name                = "state"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  database_name       = azurerm_cosmosdb_sql_database.state.name
  partition_key_paths = ["/partitionKey"]
}

# Zone-redundant Container Apps environments require an injected infrastructure
# subnet delegated to Microsoft.App/environments.
resource "azurerm_virtual_network" "cae" {
  count               = var.zone_redundant ? 1 : 0
  name                = "${var.name_prefix}-env-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = ["10.0.0.0/23"]
  tags                = local.base_tags
}

resource "azurerm_subnet" "cae" {
  count                = var.zone_redundant ? 1 : 0
  name                 = "infrastructure"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.cae[0].name
  address_prefixes     = ["10.0.0.0/23"]

  delegation {
    name = "aca"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.name_prefix}-env"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id   = var.zone_redundant ? azurerm_subnet.cae[0].id : null
  zone_redundancy_enabled    = var.zone_redundant ? true : null

  dapr_application_insights_connection_string = azurerm_application_insights.this.connection_string

  tags = local.base_tags
}

resource "azurerm_container_app_environment_dapr_component" "statestore" {
  name                         = "statestore"
  container_app_environment_id = azurerm_container_app_environment.this.id
  component_type               = "state.azure.cosmosdb"
  version                      = "v1"
  scopes                       = ["api", "worker"]

  metadata {
    name  = "url"
    value = azurerm_cosmosdb_account.this.endpoint
  }

  metadata {
    name  = "database"
    value = "state"
  }

  metadata {
    name  = "collection"
    value = "state"
  }

  metadata {
    name  = "azureClientId"
    value = azurerm_user_assigned_identity.app.client_id
  }
}

resource "azurerm_container_app_environment_dapr_component" "orderpubsub" {
  name                         = "orderpubsub"
  container_app_environment_id = azurerm_container_app_environment.this.id
  component_type               = "pubsub.azure.servicebus.topics"
  version                      = "v1"
  scopes                       = ["api", "worker"]

  metadata {
    name  = "namespaceName"
    value = "${azurerm_servicebus_namespace.this.name}.servicebus.windows.net"
  }

  metadata {
    name  = "azureClientId"
    value = azurerm_user_assigned_identity.app.client_id
  }
}

resource "azurerm_container_app" "api" {
  name                         = "${var.name_prefix}-api"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = local.base_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  dapr {
    app_id       = "api"
    app_port     = 8080
    app_protocol = "http"
  }

  ingress {
    external_enabled           = true
    target_port                = 8080
    transport                  = "auto"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 0.5
      memory = "1Gi"
    }

    http_scale_rule {
      name                = "http-rule"
      concurrent_requests = "50"
    }
  }
}

resource "azurerm_container_app" "worker" {
  name                         = "${var.name_prefix}-worker"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = local.base_tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  dapr {
    app_id       = "worker"
    app_port     = 8080
    app_protocol = "http"
  }

  template {
    min_replicas = var.min_replicas == 0 ? 1 : var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "worker"
      image  = var.worker_image
      cpu    = 0.5
      memory = "1Gi"
    }

    # KEDA Service Bus scaler. Identity-based trigger auth is configured by the
    # workload identity (azureClientId) on the Dapr component; azurerm exposes the
    # scaler metadata only.
    custom_scale_rule {
      name             = "servicebus-rule"
      custom_rule_type = "azure-servicebus"

      metadata = {
        topicName        = "orders"
        subscriptionName = "worker"
        messageCount     = "20"
        namespace        = "${azurerm_servicebus_namespace.this.name}.servicebus.windows.net"
      }
    }
  }
}

# Workload identity is a keyless Service Bus Data Owner.
resource "azurerm_role_assignment" "service_bus" {
  scope                = azurerm_servicebus_namespace.this.id
  role_definition_name = "Azure Service Bus Data Owner"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# Workload identity is a keyless Cosmos data contributor.
resource "azurerm_cosmosdb_sql_role_assignment" "app" {
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.this.name
  role_definition_id  = "${azurerm_cosmosdb_account.this.id}/sqlRoleDefinitions/${local.cosmos_data_contrib_id}"
  principal_id        = azurerm_user_assigned_identity.app.principal_id
  scope               = azurerm_cosmosdb_account.this.id
}
