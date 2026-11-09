locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "hub-spoke-network"
    workload    = "hub-spoke-network"
  }, var.tags)
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

resource "azurerm_virtual_network" "hub" {
  name                = "${var.name_prefix}-hub-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [var.hub_address_prefix]
  tags                = local.base_tags
}

resource "azurerm_subnet" "firewall" {
  name                 = "AzureFirewallSubnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.hub.name
  address_prefixes     = [cidrsubnet(var.hub_address_prefix, 10, 0)]
}

resource "azurerm_subnet" "bastion" {
  name                 = "AzureBastionSubnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.hub.name
  address_prefixes     = [cidrsubnet(var.hub_address_prefix, 10, 1)]
}

resource "azurerm_subnet" "gateway" {
  name                 = "GatewaySubnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.hub.name
  address_prefixes     = [cidrsubnet(var.hub_address_prefix, 11, 4)]
}

resource "azurerm_network_security_group" "spoke" {
  name                = "${var.name_prefix}-spoke-nsg"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags

  security_rule {
    name                       = "DenyInternetInbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_address_prefix      = "Internet"
    source_port_range          = "*"
    destination_address_prefix = "*"
    destination_port_range     = "*"
  }
}

resource "azurerm_public_ip" "firewall" {
  name                = "${var.name_prefix}-fw-pip"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = ["1", "2", "3"]
  tags                = local.base_tags
}

resource "azurerm_firewall_policy" "this" {
  name                     = "${var.name_prefix}-fw-policy"
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  sku                      = var.firewall_tier
  threat_intelligence_mode = "Alert"
  tags                     = local.base_tags
}

resource "azurerm_firewall_policy_rule_collection_group" "default" {
  name               = "DefaultNetworkRuleCollectionGroup"
  firewall_policy_id = azurerm_firewall_policy.this.id
  priority           = 200

  network_rule_collection {
    name     = "spoke-egress"
    priority = 100
    action   = "Allow"

    rule {
      name                  = "allow-https-outbound"
      protocols             = ["TCP"]
      source_addresses      = [var.spoke_address_prefix]
      destination_addresses = ["*"]
      destination_ports     = ["443"]
    }
  }
}

resource "azurerm_firewall" "this" {
  name                = "${var.name_prefix}-fw"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku_name            = "AZFW_VNet"
  sku_tier            = var.firewall_tier
  firewall_policy_id  = azurerm_firewall_policy.this.id
  zones               = ["1", "2", "3"]
  tags                = local.base_tags

  ip_configuration {
    name                 = "fwIpConfig"
    subnet_id            = azurerm_subnet.firewall.id
    public_ip_address_id = azurerm_public_ip.firewall.id
  }

  depends_on = [azurerm_firewall_policy_rule_collection_group.default]
}

resource "azurerm_public_ip" "bastion" {
  count               = var.deploy_bastion ? 1 : 0
  name                = "${var.name_prefix}-bastion-pip"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.base_tags
}

resource "azurerm_bastion_host" "this" {
  count               = var.deploy_bastion ? 1 : 0
  name                = "${var.name_prefix}-bastion"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Standard"
  tags                = local.base_tags

  ip_configuration {
    name                 = "bastionIpConfig"
    subnet_id            = azurerm_subnet.bastion.id
    public_ip_address_id = azurerm_public_ip.bastion[0].id
  }
}

resource "azurerm_route_table" "spoke" {
  name                          = "${var.name_prefix}-spoke-rt"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  bgp_route_propagation_enabled = false
  tags                          = local.base_tags

  route {
    name                   = "default-via-firewall"
    address_prefix         = "0.0.0.0/0"
    next_hop_type          = "VirtualAppliance"
    next_hop_in_ip_address = azurerm_firewall.this.ip_configuration[0].private_ip_address
  }
}

resource "azurerm_virtual_network" "spoke" {
  name                = "${var.name_prefix}-spoke-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [var.spoke_address_prefix]
  tags                = local.base_tags
}

resource "azurerm_subnet" "workload" {
  name                 = "workload"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.spoke.name
  address_prefixes     = [cidrsubnet(var.spoke_address_prefix, 8, 0)]
}

resource "azurerm_subnet" "spoke_private_endpoints" {
  name                              = "privateEndpoints"
  resource_group_name               = azurerm_resource_group.this.name
  virtual_network_name              = azurerm_virtual_network.spoke.name
  address_prefixes                  = [cidrsubnet(var.spoke_address_prefix, 8, 1)]
  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_subnet_network_security_group_association" "workload" {
  subnet_id                 = azurerm_subnet.workload.id
  network_security_group_id = azurerm_network_security_group.spoke.id
}

resource "azurerm_subnet_route_table_association" "workload" {
  subnet_id      = azurerm_subnet.workload.id
  route_table_id = azurerm_route_table.spoke.id
}

resource "azurerm_virtual_network_peering" "hub_to_spoke" {
  name                         = "hub-to-spoke"
  resource_group_name          = azurerm_resource_group.this.name
  virtual_network_name         = azurerm_virtual_network.hub.name
  remote_virtual_network_id    = azurerm_virtual_network.spoke.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_virtual_network_peering" "spoke_to_hub" {
  name                         = "spoke-to-hub"
  resource_group_name          = azurerm_resource_group.this.name
  virtual_network_name         = azurerm_virtual_network.spoke.name
  remote_virtual_network_id    = azurerm_virtual_network.hub.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
  allow_gateway_transit        = false
  use_remote_gateways          = false
}

resource "azurerm_monitor_diagnostic_setting" "firewall" {
  name                       = "fw-diagnostics"
  target_resource_id         = azurerm_firewall.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }

  enabled_metric {
    category = "AllMetrics"
  }
}
