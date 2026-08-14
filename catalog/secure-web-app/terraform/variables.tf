variable "resource_group_name" {
  type        = string
  description = "Resource group the deployment creates and owns."
}

variable "location" {
  type        = string
  description = "Azure region for all resources."
  default     = "centralus"
}

variable "environment" {
  type        = string
  description = "Environment name (dev or prod)."
  default     = "dev"
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the resource group and resources. The executor injects techlabs-managed/owner/deployment tags."
  default     = {}
}

variable "name_prefix" {
  type        = string
  description = "Prefix for resource names."
  default     = "securewa"
}

variable "app_service_sku" {
  type        = string
  description = "App Service plan SKU (P0v3, P1v3, P2v3)."
  default     = "P1v3"
}

variable "app_service_capacity" {
  type        = number
  description = "Number of App Service plan instances."
  default     = 1
}

variable "zone_redundant" {
  type        = bool
  description = "Make the App Service plan, SQL database, and gateway zone redundant."
  default     = false
}

variable "sql_sku_name" {
  type        = string
  description = "Azure SQL database SKU name."
  default     = "GP_S_Gen5_1"
}

variable "sql_admin_object_id" {
  type        = string
  description = "Entra ID object ID of the SQL admin (user or group)."
}

variable "sql_admin_login" {
  type        = string
  description = "Entra ID login name (UPN or group name) of the SQL admin."
}

variable "app_gateway_max_capacity" {
  type        = number
  description = "Application Gateway autoscale max capacity."
  default     = 3
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
