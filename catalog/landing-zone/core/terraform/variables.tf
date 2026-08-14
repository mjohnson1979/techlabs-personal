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

variable "prefix" {
  type        = string
  description = "Resource name prefix."
  default     = "techlabs"
}

variable "storage_sku" {
  type        = string
  description = "Storage redundancy (Standard_LRS, Standard_ZRS, Standard_GRS)."
  default     = "Standard_LRS"
}
