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

variable "sku" {
  type        = string
  description = "App Service plan SKU. Use a Premium v3 SKU (P1v3+) to enable zone redundancy."
  default     = "B1"
}

variable "capacity" {
  type        = number
  description = "Number of plan instances. Zone redundancy requires at least 3 on a Premium v3 plan."
  default     = 1
}

variable "zone_redundant" {
  type        = bool
  description = "Spread instances across availability zones. Requires a Premium v3 SKU and capacity >= 3."
  default     = false
}

variable "dotnet_version" {
  type        = string
  description = "Linux .NET runtime version for the web app (e.g. 8.0)."
  default     = "8.0"
}

variable "health_check_path" {
  type        = string
  description = "Relative health check path the platform uses to evict unhealthy instances."
  default     = "/health"
}
