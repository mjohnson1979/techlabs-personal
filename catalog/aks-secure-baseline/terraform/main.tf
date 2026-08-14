data "azurerm_client_config" "current" {}

locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "aks-secure-baseline"
    workload    = "aks-secure-baseline"
  }, var.tags)

  vnet_address_prefix = "10.3.0.0/16"
  acr_name            = lower("${var.name_prefix}acr")
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

resource "azurerm_virtual_network" "this" {
  name                = "${var.name_prefix}-vnet"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [local.vnet_address_prefix]
  tags                = local.base_tags
}

resource "azurerm_subnet" "aks" {
  name                 = "aks"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.3.0.0/22"]
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "privateEndpoints"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = ["10.3.4.0/24"]
}

resource "azurerm_container_registry" "this" {
  name                          = local.acr_name
  resource_group_name           = azurerm_resource_group.this.name
  location                      = azurerm_resource_group.this.location
  sku                           = "Premium"
  admin_enabled                 = false
  public_network_access_enabled = true
  zone_redundancy_enabled       = var.zone_redundant
  retention_policy_in_days      = 30
  tags                          = local.base_tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_kubernetes_cluster" "this" {
  name                      = "${var.name_prefix}-aks"
  resource_group_name       = azurerm_resource_group.this.name
  location                  = azurerm_resource_group.this.location
  dns_prefix                = "${var.name_prefix}-aks"
  kubernetes_version        = var.kubernetes_version
  sku_tier                  = var.zone_redundant ? "Standard" : "Free"
  local_account_disabled    = true
  private_cluster_enabled   = var.enable_private_cluster
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  tags                      = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    azure_rbac_enabled = true
    tenant_id          = data.azurerm_client_config.current.tenant_id
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_policy      = "cilium"
    network_data_plane  = "cilium"
    load_balancer_sku   = "standard"
    service_cidr        = "172.16.0.0/16"
    dns_service_ip      = "172.16.0.10"
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  }

  key_vault_secrets_provider {
    secret_rotation_enabled = true
  }

  default_node_pool {
    name                 = "system"
    vm_size              = var.system_node_vm_size
    node_count           = var.system_node_count
    auto_scaling_enabled = var.enable_auto_scaling
    min_count            = var.enable_auto_scaling ? var.system_node_count : null
    max_count            = var.enable_auto_scaling ? var.max_node_count : null
    vnet_subnet_id       = azurerm_subnet.aks.id
    zones                = var.zone_redundant ? ["1", "2", "3"] : null
    max_pods             = 50
    os_sku               = "AzureLinux"

    upgrade_settings {
      max_surge = "33%"
    }
  }
}

# Kubelet identity can pull from the container registry.
resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id
  principal_type       = "ServicePrincipal"
}
