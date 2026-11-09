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
  default     = "entrag"
}

variable "chat_model" {
  type        = string
  description = "Chat completion model to deploy."
  default     = "gpt-5-mini"
}

variable "chat_model_version" {
  type        = string
  description = "Chat model version."
  default     = "2025-08-07"
}

variable "chat_model_sku" {
  type        = string
  description = "Chat deployment SKU."
  default     = "GlobalStandard"
}

variable "chat_capacity" {
  type        = number
  description = "Chat deployment capacity (thousands of TPM)."
  default     = 20
}

variable "embedding_model" {
  type        = string
  description = "Embedding model to deploy."
  default     = "text-embedding-3-large"
}

variable "embedding_model_version" {
  type        = string
  description = "Embedding model version."
  default     = "1"
}

variable "search_sku" {
  type        = string
  description = "Azure AI Search SKU (basic, standard, standard2)."
  default     = "standard"
}

variable "search_replicas" {
  type        = number
  description = "Number of Azure AI Search replicas."
  default     = 1
}

variable "app_service_sku" {
  type        = string
  description = "App Service plan SKU (P0v3, P1v3, P2v3)."
  default     = "P1v3"
}

variable "zone_redundant" {
  type        = bool
  description = "Make the App Service plan zone redundant."
  default     = false
}

variable "apim_sku" {
  type        = string
  description = "API Management SKU (Developer, Standard, Premium)."
  default     = "Developer"
}

variable "apim_publisher_email" {
  type        = string
  description = "Publisher email for API Management."
  default     = "platform@contoso.com"
}

variable "apim_publisher_name" {
  type        = string
  description = "Publisher name for API Management."
  default     = "Contoso Platform"
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
