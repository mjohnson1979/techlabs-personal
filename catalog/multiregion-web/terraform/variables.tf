variable "resource_group_name" {
  type        = string
  description = "Resource group the deployment creates and owns."
}

variable "location" {
  type        = string
  description = "Azure region for the resource group (resources use primary/secondary locations)."
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
  default     = "mrweb"
}

variable "primary_location" {
  type        = string
  description = "Primary Azure region."
  default     = "eastus"
}

variable "secondary_location" {
  type        = string
  description = "Secondary Azure region."
  default     = "westus3"
}

variable "app_service_sku" {
  type        = string
  description = "App Service plan SKU (P0v3, P1v3, P2v3)."
  default     = "P1v3"
}

variable "app_service_capacity" {
  type        = number
  description = "Instances per regional App Service plan."
  default     = 1
}

variable "zone_redundant" {
  type        = bool
  description = "Make each regional App Service plan zone redundant."
  default     = false
}

variable "front_door_sku" {
  type        = string
  description = "Front Door SKU (Standard_AzureFrontDoor or Premium_AzureFrontDoor)."
  default     = "Premium_AzureFrontDoor"
}

variable "cosmos_multi_region_writes" {
  type        = bool
  description = "Enable multi-region writes on Cosmos DB."
  default     = false
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
