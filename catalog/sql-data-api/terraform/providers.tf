terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  # The backend block is injected at init time by the gallery executor
  # (azurerm state in Azure Storage in production, local state for dev). Keeping
  # it out of source lets the same module run one-click in-app and via the CLI.
}

provider "azurerm" {
  features {}
  # Subscription/credentials come from the environment: ARM_SUBSCRIPTION_ID and
  # either ARM_USE_MSI=true (App Service managed identity) or az login locally.
}
