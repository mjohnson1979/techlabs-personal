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

variable "prefix" {
  type        = string
  description = "Resource name prefix."
  default     = "techlabs"
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the resource group and resources. The executor injects techlabs-managed/owner/deployment tags."
  default     = {}
}

variable "app_service_sku" {
  type        = string
  description = "App Service plan SKU (P1v3+ to allow zone redundancy)."
  default     = "B1"
}

variable "app_capacity" {
  type        = number
  description = "App Service plan instance count."
  default     = 1
}

variable "app_zone_redundant" {
  type        = bool
  description = "Zone-redundant App Service plan (requires Premium v3 and capacity >= 3)."
  default     = false
}

variable "sql_sku_name" {
  type        = string
  description = "Azure SQL database SKU name (e.g. GP_S_Gen5_1 serverless, GP_Gen5_2 provisioned)."
  default     = "GP_S_Gen5_1"
}

variable "sql_tier" {
  type        = string
  description = "Azure SQL database tier (encoded in sku_name; kept for parity)."
  default     = "GeneralPurpose"
}

variable "sql_zone_redundant" {
  type        = bool
  description = "Make the SQL database zone redundant (prod reliability)."
  default     = false
}

variable "sql_admin_object_id" {
  type        = string
  description = "Microsoft Entra admin object id (user/group) for the SQL server. Required for Entra-only auth."
  default     = "00000000-0000-0000-0000-000000000000"
}

variable "sql_admin_login" {
  type        = string
  description = "Microsoft Entra admin display name for the SQL server."
  default     = "sql-admins"
}
