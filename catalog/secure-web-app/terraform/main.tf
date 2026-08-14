data "azurerm_client_config" "current" {}

locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "secure-web-app"
    workload    = "secure-web-app"
  }, var.tags)

  vnet_address_prefix = "10.2.0.0/16"
  zones               = var.zone_redundant ? ["1", "2", "3"] : null
  app_gateway_name    = "${var.name_prefix}-agw"
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

resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [local.vnet_address_prefix]
  tags                = local.base_tags
}

resource "azurerm_subnet" "app_gateway" {
  name                 = "appGateway"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.2.0.0/24"]
}

resource "azurerm_subnet" "app_service_integration" {
  name                 = "appServiceIntegration"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.2.1.0/24"]

  delegation {
    name = "serverFarms"
    service_delegation {
      name = "Microsoft.Web/serverFarms"
    }
  }
}

resource "azurerm_subnet" "private_endpoints" {
  name                              = "privateEndpoints"
  resource_group_name               = azurerm_resource_group.this.name
  virtual_network_name              = azurerm_virtual_network.this.name
  address_prefixes                  = ["10.2.2.0/24"]
  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_private_dns_zone" "sql" {
  name                = "privatelink.database.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.base_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "sql" {
  name                  = "${var.name_prefix}-sql-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.sql.name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = local.base_tags
}

resource "azurerm_private_dns_zone" "kv" {
  name                = "privatelink.vaultcore.azure.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.base_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "kv" {
  name                  = "${var.name_prefix}-kv-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.kv.name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = local.base_tags
}

resource "azurerm_mssql_server" "this" {
  name                          = "${var.name_prefix}-sql"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = false
  tags                          = local.base_tags

  azuread_administrator {
    login_username              = var.sql_admin_login
    object_id                   = var.sql_admin_object_id
    tenant_id                   = data.azurerm_client_config.current.tenant_id
    azuread_authentication_only = true
  }
}

resource "azurerm_mssql_database" "appdb" {
  name           = "appdb"
  server_id      = azurerm_mssql_server.this.id
  sku_name       = var.sql_sku_name
  zone_redundant = var.zone_redundant
  tags           = local.base_tags
}

resource "azurerm_private_endpoint" "sql" {
  name                = "${var.name_prefix}-sql-pe"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.base_tags

  private_service_connection {
    name                           = "${var.name_prefix}-sql-psc"
    private_connection_resource_id = azurerm_mssql_server.this.id
    subresource_names              = ["sqlServer"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "sql"
    private_dns_zone_ids = [azurerm_private_dns_zone.sql.id]
  }
}

resource "azurerm_key_vault" "this" {
  name                          = "${var.name_prefix}-kv"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = false
  tags                          = local.base_tags

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }
}

resource "azurerm_private_endpoint" "kv" {
  name                = "${var.name_prefix}-kv-pe"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.base_tags

  private_service_connection {
    name                           = "${var.name_prefix}-kv-psc"
    private_connection_resource_id = azurerm_key_vault.this.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "kv"
    private_dns_zone_ids = [azurerm_private_dns_zone.kv.id]
  }
}

resource "azurerm_service_plan" "this" {
  name                   = "${var.name_prefix}-plan"
  resource_group_name    = azurerm_resource_group.this.name
  location               = azurerm_resource_group.this.location
  os_type                = "Linux"
  sku_name               = var.app_service_sku
  worker_count           = var.app_service_capacity
  zone_balancing_enabled = var.zone_redundant
  tags                   = local.base_tags
}

resource "azurerm_linux_web_app" "this" {
  name                          = "${var.name_prefix}-app"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  service_plan_id               = azurerm_service_plan.this.id
  virtual_network_subnet_id     = azurerm_subnet.app_service_integration.id
  https_only                    = true
  public_network_access_enabled = false
  tags                          = local.base_tags

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
      dotnet_version = "8.0"
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.this.connection_string
    KeyVaultUri                           = azurerm_key_vault.this.vault_uri
    SqlConnectionString                   = "Server=tcp:${azurerm_mssql_server.this.fully_qualified_domain_name},1433;Database=${azurerm_mssql_database.appdb.name};Authentication=Active Directory Default;Encrypt=True;"
    WEBSITE_VNET_ROUTE_ALL                = "1"
  }
}

resource "azurerm_public_ip" "app_gateway" {
  name                = "${var.name_prefix}-agw-pip"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = local.zones
  tags                = local.base_tags
}

resource "azurerm_web_application_firewall_policy" "this" {
  name                = "${var.name_prefix}-waf"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags

  policy_settings {
    enabled                     = true
    mode                        = "Prevention"
    request_body_check          = true
    max_request_body_size_in_kb = 128
    file_upload_limit_in_mb     = 100
  }

  managed_rules {
    managed_rule_set {
      type    = "OWASP"
      version = "3.2"
    }
  }
}

resource "azurerm_application_gateway" "this" {
  name                = local.app_gateway_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  zones               = local.zones
  firewall_policy_id  = azurerm_web_application_firewall_policy.this.id
  tags                = local.base_tags

  sku {
    name = "WAF_v2"
    tier = "WAF_v2"
  }

  autoscale_configuration {
    min_capacity = 2
    max_capacity = var.app_gateway_max_capacity
  }

  gateway_ip_configuration {
    name      = "appGatewayIpConfig"
    subnet_id = azurerm_subnet.app_gateway.id
  }

  frontend_ip_configuration {
    name                 = "appGatewayFrontendIp"
    public_ip_address_id = azurerm_public_ip.app_gateway.id
  }

  frontend_port {
    name = "port80"
    port = 80
  }

  backend_address_pool {
    name  = "webAppBackend"
    fqdns = [azurerm_linux_web_app.this.default_hostname]
  }

  backend_http_settings {
    name                                = "httpsSettings"
    port                                = 443
    protocol                            = "Https"
    cookie_based_affinity               = "Disabled"
    pick_host_name_from_backend_address = true
    request_timeout                     = 30
    probe_name                          = "healthProbe"
  }

  http_listener {
    name                           = "httpListener"
    frontend_ip_configuration_name = "appGatewayFrontendIp"
    frontend_port_name             = "port80"
    protocol                       = "Http"
  }

  request_routing_rule {
    name                       = "routeToWebApp"
    rule_type                  = "Basic"
    priority                   = 100
    http_listener_name         = "httpListener"
    backend_address_pool_name  = "webAppBackend"
    backend_http_settings_name = "httpsSettings"
  }

  probe {
    name                                      = "healthProbe"
    protocol                                  = "Https"
    path                                      = "/health"
    interval                                  = 30
    timeout                                   = 30
    unhealthy_threshold                       = 3
    pick_host_name_from_backend_http_settings = true

    match {
      status_code = ["200-399"]
    }
  }
}
