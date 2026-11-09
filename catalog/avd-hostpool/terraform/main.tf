locals {
  base_tags = merge({
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "avd-hostpool"
    workload    = "avd-hostpool"
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

resource "azurerm_virtual_desktop_host_pool" "this" {
  name                     = "${var.name_prefix}-hp"
  resource_group_name      = azurerm_resource_group.this.name
  location                 = var.metadata_location
  type                     = "Pooled"
  load_balancer_type       = "BreadthFirst"
  preferred_app_group_type = "Desktop"
  maximum_sessions_allowed = var.max_session_limit
  start_vm_on_connect      = var.start_vm_on_connect
  validate_environment     = false
  custom_rdp_properties    = "audiocapturemode:i:1;audiomode:i:0;drivestoredirect:s:;redirectclipboard:i:0;redirectprinters:i:0;"
  tags                     = local.base_tags
}

resource "azurerm_virtual_desktop_application_group" "this" {
  name                = "${var.name_prefix}-dag"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.metadata_location
  type                = "Desktop"
  host_pool_id        = azurerm_virtual_desktop_host_pool.this.id
  friendly_name       = "Corporate Desktop"
  tags                = local.base_tags
}

resource "azurerm_virtual_desktop_workspace" "this" {
  name                = "${var.name_prefix}-ws"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.metadata_location
  friendly_name       = "Corporate Workspace"
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
  location            = var.metadata_location
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

resource "azurerm_monitor_diagnostic_setting" "hostpool" {
  name                       = "hp-diagnostics"
  target_resource_id         = azurerm_virtual_desktop_host_pool.this.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id

  enabled_log {
    category_group = "allLogs"
  }
}
