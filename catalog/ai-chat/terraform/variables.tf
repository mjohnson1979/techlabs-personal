variable "resource_group_name" {
  type        = string
  description = "Resource group the deployment creates and owns."
}

variable "location" {
  type        = string
  description = "Azure region. Azure OpenAI model availability varies by region."
  default     = "centralus"
}

variable "environment" {
  type        = string
  description = "Environment name (dev or prod)."
  default     = "dev"
}

variable "prefix" {
  type        = string
  description = "Resource name prefix."
  default     = "techlabs"
}

variable "tags" {
  type        = map(string)
  description = "Tags applied to the resource group and resources. The executor injects techlabs-managed/owner/deployment tags."
  default     = {}
}

variable "app_service_sku" {
  type        = string
  description = "App Service plan SKU (P1v3+ for zone redundancy)."
  default     = "B1"
}

variable "app_capacity" {
  type        = number
  description = "App Service plan instance count."
  default     = 1
}

variable "app_zone_redundant" {
  type        = bool
  description = "Zone-redundant App Service plan (requires Premium v3 and capacity >= 3)."
  default     = false
}

variable "chat_model_name" {
  type        = string
  description = "Chat model to deploy."
  default     = "gpt-5-mini"
}

variable "chat_model_version" {
  type        = string
  description = "Chat model version."
  default     = "2025-08-07"
}

variable "chat_model_sku" {
  type        = string
  description = "Model deployment SKU (GlobalStandard for current GPT-5 models)."
  default     = "GlobalStandard"
}

variable "chat_model_capacity" {
  type        = number
  description = "Tokens-per-minute capacity (thousands) for the model deployment."
  default     = 10
}
