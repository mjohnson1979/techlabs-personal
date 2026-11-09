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
  default     = "avs"
}

variable "avs_sku_name" {
  type        = string
  description = "AVS private cloud SKU."
  default     = "AV36"
}

variable "avs_cluster_size" {
  type        = number
  description = "Number of AVS nodes in the management cluster (minimum 3)."
  default     = 3
}

variable "avs_network_block" {
  type        = string
  description = "Address block (/22) for the AVS management network."
  default     = "10.250.0.0/22"
}

variable "vnet_address_prefixes" {
  type        = list(string)
  description = "Address space for the landing virtual network."
  default     = ["10.60.0.0/16"]
}

variable "gateway_subnet_prefix" {
  type        = string
  description = "GatewaySubnet prefix for the ExpressRoute gateway."
  default     = "10.60.0.0/27"
}

variable "shared_services_subnet_prefix" {
  type        = string
  description = "Shared services / jumpbox subnet prefix."
  default     = "10.60.1.0/24"
}

variable "enable_rbac_authorization" {
  type        = bool
  description = "Use RBAC for Key Vault data-plane access."
  default     = true
}

variable "deploy_connectivity" {
  type        = bool
  description = "Deploy the ExpressRoute gateway for hybrid connectivity."
  default     = true
}

variable "avs_express_route_authorization_id" {
  type        = string
  description = "AVS ExpressRoute circuit ID. Leave empty to deploy the gateway without a circuit connection."
  default     = ""
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}

variable "enable_avs_diagnostics" {
  type        = bool
  description = "Route AVS control-plane diagnostics to Log Analytics."
  default     = true
}
