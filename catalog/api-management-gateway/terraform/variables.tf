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
  default     = "apigw"
}

variable "apim_sku" {
  type        = string
  description = "API Management SKU (Developer or Premium)."
  default     = "Developer"
}

variable "apim_capacity" {
  type        = number
  description = "API Management capacity (scale units)."
  default     = 1
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

variable "backend_sku" {
  type        = string
  description = "Backend App Service plan SKU (P0v3, P1v3, P2v3)."
  default     = "P1v3"
}

variable "zone_redundant" {
  type        = bool
  description = "Make the backend plan zone redundant."
  default     = false
}

variable "rate_limit_calls_per_minute" {
  type        = number
  description = "Requests-per-minute rate limit applied by the gateway product."
  default     = 300
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
