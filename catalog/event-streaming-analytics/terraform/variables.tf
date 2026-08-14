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
  default     = "stream"
}

variable "event_hubs_sku" {
  type        = string
  description = "Event Hubs namespace SKU (Standard or Premium)."
  default     = "Standard"
}

variable "throughput_units" {
  type        = number
  description = "Throughput units (Standard) for the Event Hubs namespace."
  default     = 1
}

variable "partition_count" {
  type        = number
  description = "Number of partitions on the event hub."
  default     = 4
}

variable "message_retention_days" {
  type        = number
  description = "Message retention in days for the event hub."
  default     = 1
}

variable "streaming_units" {
  type        = number
  description = "Stream Analytics streaming units."
  default     = 3
}

variable "function_plan_sku" {
  type        = string
  description = "Function plan SKU (Y1 consumption, or EP1/EP2 elastic premium)."
  default     = "EP1"
}

variable "zone_redundant" {
  type        = bool
  description = "Make storage and Cosmos zone redundant."
  default     = false
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
