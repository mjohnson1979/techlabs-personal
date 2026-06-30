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
  default     = "hubspoke"
}

variable "hub_address_prefix" {
  type        = string
  description = "Address space for the hub VNet."
  default     = "10.0.0.0/16"
}

variable "spoke_address_prefix" {
  type        = string
  description = "Address space for the spoke (workload) VNet."
  default     = "10.1.0.0/16"
}

variable "firewall_tier" {
  type        = string
  description = "Azure Firewall SKU tier (Standard or Premium)."
  default     = "Standard"
}

variable "deploy_bastion" {
  type        = bool
  description = "Deploy Azure Bastion for secure RDP/SSH without public IPs on workloads."
  default     = true
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
