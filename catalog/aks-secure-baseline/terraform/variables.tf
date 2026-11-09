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
  default     = "aksbase"
}

variable "kubernetes_version" {
  type        = string
  description = "Kubernetes version."
  default     = "1.30"
}

variable "system_node_vm_size" {
  type        = string
  description = "System node pool VM size."
  default     = "Standard_D4s_v5"
}

variable "system_node_count" {
  type        = number
  description = "System node pool node count."
  default     = 3
}

variable "enable_auto_scaling" {
  type        = bool
  description = "Enable cluster autoscaler on the system pool."
  default     = true
}

variable "max_node_count" {
  type        = number
  description = "Maximum nodes when autoscaling."
  default     = 5
}

variable "enable_private_cluster" {
  type        = bool
  description = "Deploy the cluster as a private cluster (no public API server)."
  default     = true
}

variable "zone_redundant" {
  type        = bool
  description = "Spread node pool across availability zones and enable Standard tiers."
  default     = true
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
