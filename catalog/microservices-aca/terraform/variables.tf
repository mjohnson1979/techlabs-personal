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
  default     = "acamesh"
}

variable "api_image" {
  type        = string
  description = "Container image for the API service."
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "worker_image" {
  type        = string
  description = "Container image for the worker service."
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "min_replicas" {
  type        = number
  description = "Minimum replicas per app."
  default     = 0
}

variable "max_replicas" {
  type        = number
  description = "Maximum replicas per app."
  default     = 10
}

variable "zone_redundant" {
  type        = bool
  description = "Make the Container Apps environment zone redundant."
  default     = false
}

variable "service_bus_sku" {
  type        = string
  description = "Service Bus SKU (Standard or Premium)."
  default     = "Standard"
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
