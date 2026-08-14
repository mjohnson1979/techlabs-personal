variable "resource_group_name" {
  type        = string
  description = "Resource group the deployment creates and owns."
}

variable "location" {
  type        = string
  description = "Azure region for the regional resources (network, storage, Key Vault, session hosts)."
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

variable "avd_metadata_location" {
  type        = string
  description = "Metadata region for the AVD host pool, application group, and workspace control-plane objects."
  default     = "eastus"
}

variable "max_session_limit" {
  type        = number
  description = "Maximum user sessions per session host VM."
  default     = 10
}

variable "start_vm_on_connect" {
  type        = bool
  description = "Start stopped session host VMs when users connect."
  default     = true
}

variable "enable_scaling_plan" {
  type        = bool
  description = "Deploy an AVD scaling plan for the host pool."
  default     = true
}

variable "scaling_time_zone" {
  type        = string
  description = "Time zone used by the AVD scaling schedule."
  default     = "Eastern Standard Time"
}

variable "vnet_address_prefixes" {
  type        = list(string)
  description = "Address space for the landing virtual network."
  default     = ["10.40.0.0/16"]
}

variable "session_hosts_subnet_prefix" {
  type        = string
  description = "Session-hosts subnet prefix."
  default     = "10.40.1.0/24"
}

variable "private_endpoint_subnet_prefix" {
  type        = string
  description = "Private-endpoints subnet prefix."
  default     = "10.40.2.0/24"
}

variable "enable_rbac_authorization" {
  type        = bool
  description = "Use RBAC for Key Vault data-plane access."
  default     = true
}

variable "fslogix_profile_share_name" {
  type        = string
  description = "Name of the Azure Files share used for FSLogix profile containers."
  default     = "profiles"
}

variable "retention_in_days" {
  type        = number
  description = "Log Analytics retention in days."
  default     = 30
}

variable "deploy_storage_private_endpoint" {
  type        = bool
  description = "Deploy a private endpoint and private DNS zone for the FSLogix file share."
  default     = true
}

variable "deploy_session_hosts" {
  type        = bool
  description = "Deploy session host VMs and register them with the host pool. Requires an admin password."
  default     = false
}

variable "session_host_count" {
  type        = number
  description = "Number of session host VMs to deploy when deploy_session_hosts is true."
  default     = 2
}

variable "session_host_vm_size" {
  type        = string
  description = "VM size for the session hosts."
  default     = "Standard_D4s_v5"
}

variable "session_host_admin_username" {
  type        = string
  description = "Local administrator username for the session hosts."
  default     = "avdadmin"
}

variable "session_host_admin_password" {
  type        = string
  description = "Local administrator password for the session hosts. Required when deploy_session_hosts is true."
  default     = ""
  sensitive   = true
}

variable "assign_desktop_user_access" {
  type        = bool
  description = "Grant a user or group the Desktop Virtualization User role on the application group."
  default     = false
}

variable "desktop_user_principal_id" {
  type        = string
  description = "Object ID of the user or group to grant desktop access. Required when assign_desktop_user_access is true."
  default     = ""
}

variable "desktop_user_principal_type" {
  type        = string
  description = "Type of the principal being granted desktop access (User, Group, ServicePrincipal)."
  default     = "Group"
}

variable "avd_agent_dsc_package_url" {
  type        = string
  description = "URL of the AVD agent DSC configuration package used to register session hosts."
  default     = "https://wvdportalstorageblob.blob.core.windows.net/galleryartifacts/Configuration_1.0.02790.438.zip"
}
