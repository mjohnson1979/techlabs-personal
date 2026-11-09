data "azurerm_client_config" "current" {}

locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "azure-vmware-solution-enterprise"
    workload    = "azure-vmware-solution-enterprise"
  }, var.tags)
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.base_tags
}

# --- Monitoring ---
resource "azurerm_log_analytics_workspace" "this" {
  name                = "${var.name_prefix}-law"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = var.retention_in_days
  tags                = local.base_tags
}

resource "azurerm_application_insights" "this" {
  name                = "${var.name_prefix}-appi"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.this.id
  tags                = local.base_tags
}

# --- Security ---
resource "azurerm_key_vault" "this" {
  name                       = "${var.name_prefix}-kv"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = var.enable_rbac_authorization
  soft_delete_retention_days = 7
  tags                       = local.base_tags
}

resource "azurerm_network_security_group" "shared" {
  name                = "${var.name_prefix}-shared-nsg"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags

  security_rule {
    name                       = "AllowHttpsInboundFromCorp"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "VirtualNetwork"
    destination_address_prefix = "*"
    access                     = "Allow"
    priority                   = 300
    direction                  = "Inbound"
  }
}

# --- Networking ---
resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = var.vnet_address_prefixes
  tags                = local.base_tags
}

resource "azurerm_subnet" "gateway" {
  name                 = "GatewaySubnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.gateway_subnet_prefix]
}

resource "azurerm_subnet" "shared_services" {
  name                 = "shared-services"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.shared_services_subnet_prefix]
}

# --- AVS private cloud ---
resource "azurerm_vmware_private_cloud" "this" {
  name                        = "${var.name_prefix}-private-cloud"
  resource_group_name         = azurerm_resource_group.this.name
  location                    = azurerm_resource_group.this.location
  sku_name                    = var.avs_sku_name
  network_subnet_cidr         = var.avs_network_block
  internet_connection_enabled = false
  tags                        = local.base_tags

  management_cluster {
    size = var.avs_cluster_size
  }
}

# --- Connectivity (optional) ---
resource "azurerm_public_ip" "gateway" {
  count               = var.deploy_connectivity ? 1 : 0
  name                = "${var.name_prefix}-ergw-pip"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.base_tags
}

resource "azurerm_virtual_network_gateway" "express_route" {
  count               = var.deploy_connectivity ? 1 : 0
  name                = "${var.name_prefix}-ergw"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  type                = "ExpressRoute"
  vpn_type            = "RouteBased"
  sku                 = "ErGw2AZ"
  tags                = local.base_tags

  ip_configuration {
    name                          = "gateway-ipconfig"
    public_ip_address_id          = azurerm_public_ip.gateway[0].id
    private_ip_address_allocation = "Dynamic"
    subnet_id                     = azurerm_subnet.gateway.id
  }
}

resource "azurerm_virtual_network_gateway_connection" "express_route" {
  count                      = var.deploy_connectivity && var.avs_express_route_authorization_id != "" ? 1 : 0
  name                       = "${var.name_prefix}-avs-er-conn"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  type                       = "ExpressRoute"
  virtual_network_gateway_id = azurerm_virtual_network_gateway.express_route[0].id
  express_route_circuit_id   = var.avs_express_route_authorization_id
  tags                       = local.base_tags
}

# --- AVS diagnostics (optional) ---
resource "azurerm_monitor_diagnostic_setting" "avs" {
  count                      = var.enable_avs_diagnostics ? 1 : 0
  name                       = "avs-diagnostics"
  target_resource_id         = azurerm_vmware_private_cloud.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}
