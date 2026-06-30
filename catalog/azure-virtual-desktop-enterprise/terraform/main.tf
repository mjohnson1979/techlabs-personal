locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "azure-virtual-desktop-enterprise"
    workload    = "azure-virtual-desktop-enterprise"
  }, var.tags)

  fslogix_storage_name = substr(lower(replace("${var.name_prefix}fsl${random_string.suffix.result}", "-", "")), 0, 24)
  session_host_count   = var.deploy_session_hosts ? var.session_host_count : 0
}

resource "random_string" "suffix" {
  length  = 13
  upper   = false
  special = false
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

data "azurerm_client_config" "current" {}

resource "azurerm_network_security_group" "session_hosts" {
  name                = "${var.name_prefix}-sessionhosts-nsg"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags

  security_rule {
    name                       = "AllowRdpFromCorp"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "3389"
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

resource "azurerm_subnet" "session_hosts" {
  name                 = "session-hosts"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.session_hosts_subnet_prefix]
}

resource "azurerm_subnet" "private_endpoints" {
  name                              = "private-endpoints"
  resource_group_name               = azurerm_resource_group.this.name
  virtual_network_name              = azurerm_virtual_network.this.name
  address_prefixes                  = [var.private_endpoint_subnet_prefix]
  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_subnet_network_security_group_association" "session_hosts" {
  subnet_id                 = azurerm_subnet.session_hosts.id
  network_security_group_id = azurerm_network_security_group.session_hosts.id
}

# --- FSLogix profile storage ---
resource "azurerm_storage_account" "fslogix" {
  name                            = local.fslogix_storage_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  account_kind                    = "StorageV2"
  access_tier                     = "Hot"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  public_network_access_enabled   = true
  shared_access_key_enabled       = true
  tags                            = local.base_tags
}

resource "azurerm_storage_share" "profiles" {
  name               = var.fslogix_profile_share_name
  storage_account_id = azurerm_storage_account.fslogix.id
  quota              = 100
  enabled_protocol   = "SMB"
  access_tier        = "TransactionOptimized"
}

resource "azurerm_private_dns_zone" "fslogix" {
  count               = var.deploy_storage_private_endpoint ? 1 : 0
  name                = "privatelink.file.core.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.base_tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "fslogix" {
  count                 = var.deploy_storage_private_endpoint ? 1 : 0
  name                  = "${var.name_prefix}-fslogix-link"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.fslogix[0].name
  virtual_network_id    = azurerm_virtual_network.this.id
  tags                  = local.base_tags
}

resource "azurerm_private_endpoint" "fslogix" {
  count               = var.deploy_storage_private_endpoint ? 1 : 0
  name                = "${var.name_prefix}-fslogix-pe"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.base_tags

  private_service_connection {
    name                           = "${var.name_prefix}-fslogix-psc"
    private_connection_resource_id = azurerm_storage_account.fslogix.id
    subresource_names              = ["file"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "fslogix"
    private_dns_zone_ids = [azurerm_private_dns_zone.fslogix[0].id]
  }
}

# --- AVD control plane ---
resource "azurerm_virtual_desktop_host_pool" "this" {
  name                     = "${var.name_prefix}-hp"
  resource_group_name      = azurerm_resource_group.this.name
  location                 = var.avd_metadata_location
  type                     = "Pooled"
  load_balancer_type       = "BreadthFirst"
  preferred_app_group_type = "Desktop"
  maximum_sessions_allowed = var.max_session_limit
  start_vm_on_connect      = var.start_vm_on_connect
  validate_environment     = false
  tags                     = local.base_tags
}

resource "time_offset" "registration_expiry" {
  offset_days = 14
}

resource "azurerm_virtual_desktop_host_pool_registration_info" "this" {
  hostpool_id     = azurerm_virtual_desktop_host_pool.this.id
  expiration_date = time_offset.registration_expiry.rfc3339
}

resource "azurerm_virtual_desktop_application_group" "this" {
  name                = "${var.name_prefix}-dag"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.avd_metadata_location
  type                = "Desktop"
  host_pool_id        = azurerm_virtual_desktop_host_pool.this.id
  friendly_name       = "Enterprise Desktop"
  tags                = local.base_tags
}

resource "azurerm_virtual_desktop_workspace" "this" {
  name                = "${var.name_prefix}-ws"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.avd_metadata_location
  friendly_name       = "Enterprise Workspace"
  tags                = local.base_tags
}

resource "azurerm_virtual_desktop_workspace_application_group_association" "this" {
  workspace_id         = azurerm_virtual_desktop_workspace.this.id
  application_group_id = azurerm_virtual_desktop_application_group.this.id
}

resource "azurerm_virtual_desktop_scaling_plan" "this" {
  count               = var.enable_scaling_plan ? 1 : 0
  name                = "${var.name_prefix}-sp"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.avd_metadata_location
  time_zone           = var.scaling_time_zone
  tags                = local.base_tags

  schedule {
    name                                 = "weekday"
    days_of_week                         = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    ramp_up_start_time                   = "07:00"
    ramp_up_load_balancing_algorithm     = "BreadthFirst"
    ramp_up_minimum_hosts_percent        = 20
    ramp_up_capacity_threshold_percent   = 60
    peak_start_time                      = "09:00"
    peak_load_balancing_algorithm        = "BreadthFirst"
    ramp_down_start_time                 = "18:00"
    ramp_down_load_balancing_algorithm   = "DepthFirst"
    ramp_down_minimum_hosts_percent      = 10
    ramp_down_capacity_threshold_percent = 90
    ramp_down_force_logoff_users         = false
    ramp_down_wait_time_minutes          = 30
    ramp_down_notification_message       = "You will be logged off soon. Please save your work."
    ramp_down_stop_hosts_when            = "ZeroSessions"
    off_peak_start_time                  = "20:00"
    off_peak_load_balancing_algorithm    = "DepthFirst"
  }

  host_pool {
    hostpool_id          = azurerm_virtual_desktop_host_pool.this.id
    scaling_plan_enabled = true
  }
}

resource "azurerm_monitor_diagnostic_setting" "host_pool" {
  name                       = "avd-hostpool-diagnostics"
  target_resource_id         = azurerm_virtual_desktop_host_pool.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }
}

resource "azurerm_monitor_diagnostic_setting" "workspace" {
  name                       = "avd-workspace-diagnostics"
  target_resource_id         = azurerm_virtual_desktop_workspace.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }
}

# --- Session hosts (optional) ---
resource "azurerm_network_interface" "session_host" {
  count               = local.session_host_count
  name                = "${var.name_prefix}-sh-${count.index}-nic"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.base_tags

  ip_configuration {
    name                          = "ipconfig"
    subnet_id                     = azurerm_subnet.session_hosts.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_windows_virtual_machine" "session_host" {
  count               = local.session_host_count
  name                = "${var.name_prefix}-sh-${count.index}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  size                = var.session_host_vm_size
  computer_name       = "${var.name_prefix}sh${count.index}"
  admin_username      = var.session_host_admin_username
  admin_password      = var.session_host_admin_password
  network_interface_ids = [
    azurerm_network_interface.session_host[count.index].id
  ]
  secure_boot_enabled = true
  vtpm_enabled        = true
  tags                = local.base_tags

  identity {
    type = "SystemAssigned"
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "MicrosoftWindowsDesktop"
    offer     = "windows-11"
    sku       = "win11-23h2-avd"
    version   = "latest"
  }
}

resource "azurerm_virtual_machine_extension" "aad_join" {
  count                      = local.session_host_count
  name                       = "AADLoginForWindows"
  virtual_machine_id         = azurerm_windows_virtual_machine.session_host[count.index].id
  publisher                  = "Microsoft.Azure.ActiveDirectory"
  type                       = "AADLoginForWindows"
  type_handler_version       = "2.0"
  auto_upgrade_minor_version = true
}

resource "azurerm_virtual_machine_extension" "avd_agent" {
  count                      = local.session_host_count
  name                       = "Microsoft.PowerShell.DSC"
  virtual_machine_id         = azurerm_windows_virtual_machine.session_host[count.index].id
  publisher                  = "Microsoft.Powershell"
  type                       = "DSC"
  type_handler_version       = "2.73"
  auto_upgrade_minor_version = true

  settings = jsonencode({
    modulesUrl            = var.avd_agent_dsc_package_url
    configurationFunction = "Configuration.ps1\\AddSessionHost"
    properties = {
      hostPoolName = azurerm_virtual_desktop_host_pool.this.name
      aadJoin      = true
    }
  })

  protected_settings = jsonencode({
    properties = {
      registrationInfoToken = azurerm_virtual_desktop_host_pool_registration_info.this.token
    }
  })

  depends_on = [
    azurerm_virtual_machine_extension.aad_join
  ]
}

# --- Desktop access (optional) ---
resource "azurerm_role_assignment" "desktop_access" {
  count                = var.assign_desktop_user_access ? 1 : 0
  scope                = azurerm_virtual_desktop_application_group.this.id
  role_definition_name = "Desktop Virtualization User"
  principal_id         = var.desktop_user_principal_id
  principal_type       = var.desktop_user_principal_type
}
