locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "api-management-gateway"
    workload    = "api-management-gateway"
  }, var.tags)

  vnet_address_prefix = "10.4.0.0/16"
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

resource "azurerm_network_security_group" "apim" {
  name                = "${var.name_prefix}-apim-nsg"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags

  security_rule {
    name                       = "AllowApiManagementManagement"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_address_prefix      = "ApiManagement"
    source_port_range          = "*"
    destination_address_prefix = "VirtualNetwork"
    destination_port_range     = "3443"
  }

  security_rule {
    name                       = "AllowAzureLoadBalancer"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_address_prefix      = "AzureLoadBalancer"
    source_port_range          = "*"
    destination_address_prefix = "VirtualNetwork"
    destination_port_range     = "6390"
  }

  security_rule {
    name                       = "AllowHttpsInbound"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_address_prefix      = "Internet"
    source_port_range          = "*"
    destination_address_prefix = "VirtualNetwork"
    destination_port_range     = "443"
  }
}

resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [local.vnet_address_prefix]
  tags                = local.base_tags
}

resource "azurerm_subnet" "apim" {
  name                 = "apim"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.4.0.0/24"]
}

resource "azurerm_subnet" "backend" {
  name                 = "backend"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.4.1.0/24"]

  delegation {
    name = "serverFarms"
    service_delegation {
      name    = "Microsoft.Web/serverFarms"
      actions = ["Microsoft.Network/virtualNetworks/subnets/action"]
    }
  }
}

resource "azurerm_subnet_network_security_group_association" "apim" {
  subnet_id                 = azurerm_subnet.apim.id
  network_security_group_id = azurerm_network_security_group.apim.id
}

resource "azurerm_service_plan" "backend" {
  name                   = "${var.name_prefix}-backend-plan"
  resource_group_name    = azurerm_resource_group.this.name
  location               = azurerm_resource_group.this.location
  os_type                = "Linux"
  sku_name               = var.backend_sku
  worker_count           = var.zone_redundant ? 3 : 1
  zone_balancing_enabled = var.zone_redundant
  tags                   = local.base_tags
}

resource "azurerm_linux_web_app" "backend" {
  name                          = "${var.name_prefix}-backend"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  service_plan_id               = azurerm_service_plan.backend.id
  https_only                    = true
  virtual_network_subnet_id     = azurerm_subnet.backend.id
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
    WEBSITE_VNET_ROUTE_ALL                = "1"
  }
}

resource "azurerm_api_management" "this" {
  name                 = "${var.name_prefix}-apim"
  resource_group_name  = azurerm_resource_group.this.name
  location             = azurerm_resource_group.this.location
  publisher_name       = var.apim_publisher_name
  publisher_email      = var.apim_publisher_email
  sku_name             = "${var.apim_sku}_${var.apim_capacity}"
  virtual_network_type = "External"
  tags                 = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  virtual_network_configuration {
    subnet_id = azurerm_subnet.apim.id
  }
}

resource "azurerm_api_management_logger" "appinsights" {
  name                = "appinsights"
  api_management_name = azurerm_api_management.this.name
  resource_group_name = azurerm_resource_group.this.name
  resource_id         = azurerm_application_insights.this.id

  application_insights {
    connection_string = azurerm_application_insights.this.connection_string
  }
}

resource "azurerm_api_management_api" "backend" {
  name                  = "backend-api"
  api_management_name   = azurerm_api_management.this.name
  resource_group_name   = azurerm_resource_group.this.name
  revision              = "1"
  display_name          = "Backend API"
  path                  = "api"
  protocols             = ["https"]
  service_url           = "https://${azurerm_linux_web_app.backend.default_hostname}"
  subscription_required = true
}

resource "azurerm_api_management_product" "standard" {
  product_id            = "standard"
  api_management_name   = azurerm_api_management.this.name
  resource_group_name   = azurerm_resource_group.this.name
  display_name          = "Standard"
  description           = "Rate-limited access to backend APIs."
  subscription_required = true
  approval_required     = true
  published             = true
}

resource "azurerm_api_management_product_api" "link" {
  product_id          = azurerm_api_management_product.standard.product_id
  api_name            = azurerm_api_management_api.backend.name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = azurerm_resource_group.this.name
}

resource "azurerm_api_management_product_policy" "standard" {
  product_id          = azurerm_api_management_product.standard.product_id
  api_management_name = azurerm_api_management.this.name
  resource_group_name = azurerm_resource_group.this.name

  xml_content = "<policies><inbound><rate-limit calls=\"${var.rate_limit_calls_per_minute}\" renewal-period=\"60\" /><base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>"
}
