locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "container-app"
  }, var.tags)
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

# Zone redundancy for a Container Apps environment requires a delegated
# infrastructure subnet (the azurerm provider enforces the pairing). Only create
# the network when prod asks for it so dev stays a lean consumption environment.
resource "azurerm_virtual_network" "this" {
  count               = var.zone_redundant ? 1 : 0
  name                = "${var.prefix}-${var.environment}-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = ["10.0.0.0/16"]
  tags                = local.base_tags
}

resource "azurerm_subnet" "cae" {
  count                = var.zone_redundant ? 1 : 0
  name                 = "container-apps"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this[0].name
  address_prefixes     = ["10.0.0.0/23"]

  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_container_app_environment" "this" {
  name                       = "${var.prefix}-${var.environment}-cae"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id   = var.zone_redundant ? azurerm_subnet.cae[0].id : null
  zone_redundancy_enabled    = var.zone_redundant ? true : null
  tags                       = local.base_tags
}

resource "azurerm_container_app" "this" {
  name                         = "${var.prefix}-${var.environment}-app"
  resource_group_name          = azurerm_resource_group.this.name
  container_app_environment_id = azurerm_container_app_environment.this.id
  revision_mode                = "Single"
  tags                         = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "app"
      image  = var.container_image
      cpu    = var.cpu
      memory = var.memory
    }

    http_scale_rule {
      name                = "http-scale"
      concurrent_requests = "50"
    }
  }

  ingress {
    external_enabled           = true
    target_port                = var.target_port
    transport                  = "auto"
    allow_insecure_connections = false

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }
}
