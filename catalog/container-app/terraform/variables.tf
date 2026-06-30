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

variable "container_image" {
  type        = string
  description = "Container image to run."
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

variable "cpu" {
  type        = number
  description = "vCPU allocated per replica (must pair with a valid memory value)."
  default     = 0.5
}

variable "memory" {
  type        = string
  description = "Memory allocated per replica (e.g. 1.0Gi)."
  default     = "1.0Gi"
}

variable "min_replicas" {
  type        = number
  description = "Minimum replicas. 0 enables scale-to-zero (dev); 1+ avoids cold starts (prod)."
  default     = 0
}

variable "max_replicas" {
  type        = number
  description = "Maximum replicas the app can scale out to."
  default     = 5
}

variable "zone_redundant" {
  type        = bool
  description = "Make the Container Apps environment zone redundant (prod reliability)."
  default     = false
}

variable "target_port" {
  type        = number
  description = "Target port the container listens on."
  default     = 80
}
