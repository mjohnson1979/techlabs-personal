variable "resource_group_name" {
  type        = string
  description = "Name of the resource group to create."
  default     = "rg-sql-private-endpoint-dev-eastus2-001"
}

variable "location" {
  type        = string
  description = "Azure region for all regional resources."
  default     = "eastus2"
}

variable "environment" {
  type        = string
  description = "Environment name used in resource names and tags."
  default     = "dev"

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be dev or prod."
  }
}

variable "prefix" {
  type        = string
  description = "Short resource name prefix."
  default     = "techlabs"
}

variable "vm_size" {
  type        = string
  description = "SQL Server VM size."
  default     = "Standard_D4s_v5"
}

variable "admin_username" {
  type        = string
  description = "Local administrator username for the SQL Server VM."
  default     = "sqladmin"
}

variable "admin_password" {
  type        = string
  description = "Local administrator password for the SQL Server VM."
  default     = ""
  sensitive   = true
}

variable "storage_account_tier" {
  type        = string
  description = "Storage account performance tier."
  default     = "Standard"
}

variable "storage_account_replication_type" {
  type        = string
  description = "Storage account replication type."
  default     = "LRS"
}

variable "vnet_address_prefix" {
  type        = string
  description = "Address space assigned to the virtual network."
  default     = "10.20.0.0/16"
}

variable "vm_subnet_prefix" {
  type        = string
  description = "Address prefix assigned to the SQL VM subnet."
  default     = "10.20.1.0/24"
}

variable "private_endpoint_subnet_prefix" {
  type        = string
  description = "Address prefix assigned to the private endpoint subnet."
  default     = "10.20.2.0/24"
}

variable "bastion_subnet_prefix" {
  type        = string
  description = "Address prefix assigned to Azure Bastion. Must be /26 or larger."
  default     = "10.20.3.0/26"
}