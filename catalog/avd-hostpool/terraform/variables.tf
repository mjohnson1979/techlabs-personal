variable "resource_group_name" {
  type        = string
  description = "Resource group the deployment creates and owns."
}

variable "location" {
  type        = string
  description = "Azure region for session hosts and the Log Analytics workspace."
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
  default     = "avd"
}

variable "metadata_location" {
  type        = string
  description = "Metadata region for AVD objects (host pool, app group, workspace)."
  default     = "eastus"
}

variable "max_session_limit" {
  type        = number
  description = "Maximum sessions per session host."
  default     = 10
}

variable "start_vm_on_connect" {
  type        = bool
  description = "Start session host VMs on connect to save cost."
  default     = true
}

variable "enable_scaling_plan" {
  type        = bool
  description = "Enable the autoscaling plan."
  default     = true
}

variable "scaling_time_zone" {
  type        = string
  description = "Time zone for the scaling plan schedule."
  default     = "Eastern Standard Time"
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}
