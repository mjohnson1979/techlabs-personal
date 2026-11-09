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
  default     = "lakehs"
}

variable "lake_storage_sku" {
  type        = string
  description = "Storage redundancy for the data lake (Standard_LRS, Standard_ZRS, Standard_GRS)."
  default     = "Standard_LRS"
}

variable "databricks_sku" {
  type        = string
  description = "Databricks workspace SKU (standard or premium)."
  default     = "premium"
}

variable "databricks_no_public_ip" {
  type        = bool
  description = "Deploy Databricks with no public IP (secure cluster connectivity)."
  default     = true
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
