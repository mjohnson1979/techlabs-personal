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
  default     = "intwf"
}

variable "workflow_sku" {
  type        = string
  description = "Logic Apps Standard plan SKU (WS1, WS2, WS3)."
  default     = "WS1"
}

variable "service_bus_sku" {
  type        = string
  description = "Service Bus SKU (Standard or Premium)."
  default     = "Standard"
}

variable "zone_redundant" {
  type        = bool
  description = "Request zone redundancy for the Service Bus namespace (Premium namespaces are zone redundant automatically)."
  default     = false
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
