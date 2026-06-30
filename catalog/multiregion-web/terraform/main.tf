locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "multiregion-web"
    workload    = "multiregion-web"
  }, var.tags)

  cosmos_name = lower("${var.name_prefix}-cosmos")
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.base_tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name_prefix}-law"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.primary_location
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days
  tags                = local.base_tags
}

resource "azurerm_application_insights" "this" {
  name                = "${var.name_prefix}-ai"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.primary_location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.this.id
  tags                = local.base_tags
}

resource "azurerm_cosmosdb_account" "this" {
  name                             = local.cosmos_name
  resource_group_name              = azurerm_resource_group.this.name
  location                         = var.primary_location
  offer_type                       = "Standard"
  kind                             = "GlobalDocumentDB"
  local_authentication_enabled     = false
  automatic_failover_enabled       = true
  multiple_write_locations_enabled = var.cosmos_multi_region_writes
  minimal_tls_version              = "Tls12"
  tags                             = local.base_tags

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = var.primary_location
    failover_priority = 0
    zone_redundant    = var.zone_redundant
  }

  geo_location {
    location          = var.secondary_location
    failover_priority = 1
    zone_redundant    = var.zone_redundant
  }
}

resource "azurerm_service_plan" "primary" {
  name                   = "${var.name_prefix}-plan-primary"
  resource_group_name    = azurerm_resource_group.this.name
  location               = var.primary_location
  os_type                = "Linux"
  sku_name               = var.app_service_sku
  worker_count           = var.app_service_capacity
  zone_balancing_enabled = var.zone_redundant
  tags                   = local.base_tags
}

resource "azurerm_service_plan" "secondary" {
  name                   = "${var.name_prefix}-plan-secondary"
  resource_group_name    = azurerm_resource_group.this.name
  location               = var.secondary_location
  os_type                = "Linux"
  sku_name               = var.app_service_sku
  worker_count           = var.app_service_capacity
  zone_balancing_enabled = var.zone_redundant
  tags                   = local.base_tags
}

resource "azurerm_linux_web_app" "primary" {
  name                = "${var.name_prefix}-app-primary"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.primary_location
  service_plan_id     = azurerm_service_plan.primary.id
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
    ip_restriction_default_action     = "Deny"

    application_stack {
      dotnet_version = "8.0"
    }

    ip_restriction {
      name        = "AllowFrontDoor"
      priority    = 100
      action      = "Allow"
      service_tag = "AzureFrontDoor.Backend"

      headers {
        x_azure_fdid = [azurerm_cdn_frontdoor_profile.this.resource_guid]
      }
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string
    AZURE_COSMOS_ENDPOINT                 = azurerm_cosmosdb_account.this.endpoint
    REGION                                = var.primary_location
  }
}

resource "azurerm_linux_web_app" "secondary" {
  name                = "${var.name_prefix}-app-secondary"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.secondary_location
  service_plan_id     = azurerm_service_plan.secondary.id
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
    ip_restriction_default_action     = "Deny"

    application_stack {
      dotnet_version = "8.0"
    }

    ip_restriction {
      name        = "AllowFrontDoor"
      priority    = 100
      action      = "Allow"
      service_tag = "AzureFrontDoor.Backend"

      headers {
        x_azure_fdid = [azurerm_cdn_frontdoor_profile.this.resource_guid]
      }
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string
    AZURE_COSMOS_ENDPOINT                 = azurerm_cosmosdb_account.this.endpoint
    REGION                                = var.secondary_location
  }
}

resource "azurerm_cdn_frontdoor_profile" "this" {
  name                     = "${var.name_prefix}-afd"
  resource_group_name      = azurerm_resource_group.this.name
  sku_name                 = var.front_door_sku
  response_timeout_seconds = 60
  tags                     = local.base_tags
}

resource "azurerm_cdn_frontdoor_endpoint" "this" {
  name                     = "${var.name_prefix}-endpoint"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id
  enabled                  = true
  tags                     = local.base_tags
}

resource "azurerm_cdn_frontdoor_origin_group" "web" {
  name                     = "web-origins"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id

  load_balancing {
    sample_size                        = 4
    successful_samples_required        = 3
    additional_latency_in_milliseconds = 50
  }

  health_probe {
    path                = "/health"
    request_type        = "HEAD"
    protocol            = "Https"
    interval_in_seconds = 30
  }
}

resource "azurerm_cdn_frontdoor_origin" "primary" {
  name                           = "primary"
  cdn_frontdoor_origin_group_id  = azurerm_cdn_frontdoor_origin_group.web.id
  host_name                      = azurerm_linux_web_app.primary.default_hostname
  origin_host_header             = azurerm_linux_web_app.primary.default_hostname
  http_port                      = 80
  https_port                     = 443
  priority                       = 1
  weight                         = 1000
  enabled                        = true
  certificate_name_check_enabled = true
}

resource "azurerm_cdn_frontdoor_origin" "secondary" {
  name                           = "secondary"
  cdn_frontdoor_origin_group_id  = azurerm_cdn_frontdoor_origin_group.web.id
  host_name                      = azurerm_linux_web_app.secondary.default_hostname
  origin_host_header             = azurerm_linux_web_app.secondary.default_hostname
  http_port                      = 80
  https_port                     = 443
  priority                       = 1
  weight                         = 1000
  enabled                        = true
  certificate_name_check_enabled = true
}

resource "azurerm_cdn_frontdoor_route" "default" {
  name                          = "default-route"
  cdn_frontdoor_endpoint_id     = azurerm_cdn_frontdoor_endpoint.this.id
  cdn_frontdoor_origin_group_id = azurerm_cdn_frontdoor_origin_group.web.id
  cdn_frontdoor_origin_ids      = [azurerm_cdn_frontdoor_origin.primary.id, azurerm_cdn_frontdoor_origin.secondary.id]
  supported_protocols           = ["Https"]
  patterns_to_match             = ["/*"]
  forwarding_protocol           = "HttpsOnly"
  link_to_default_domain        = true
  https_redirect_enabled        = true
}

resource "azurerm_cdn_frontdoor_firewall_policy" "this" {
  name                = "${var.name_prefix}waf"
  resource_group_name = azurerm_resource_group.this.name
  sku_name            = var.front_door_sku
  enabled             = true
  mode                = "Prevention"
  tags                = local.base_tags

  managed_rule {
    type    = "Microsoft_DefaultRuleSet"
    version = "2.1"
    action  = "Block"
  }
}

resource "azurerm_cdn_frontdoor_security_policy" "waf" {
  name                     = "waf-association"
  cdn_frontdoor_profile_id = azurerm_cdn_frontdoor_profile.this.id

  security_policies {
    firewall {
      cdn_frontdoor_firewall_policy_id = azurerm_cdn_frontdoor_firewall_policy.this.id

      association {
        domain {
          cdn_frontdoor_domain_id = azurerm_cdn_frontdoor_endpoint.this.id
        }
        patterns_to_match = ["/*"]
      }
    }
  }
}
