locals {
  instance        = "001"
  resource_suffix = "${var.prefix}-${var.environment}-${var.location}-${local.instance}"
  storage_name    = substr("st${lower(replace(var.prefix, "-", ""))}${var.environment}${substr(md5(azurerm_resource_group.this.id), 0, 8)}", 0, 24)
  tags = {
    Environment = var.environment
    Owner       = "AzureTechLabs"
    UseCase     = "sql-private-endpoint"
  }
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "this" {
  name                = "vnet-${local.resource_suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [var.vnet_address_prefix]
  tags                = local.tags
}

resource "azurerm_subnet" "sql" {
  name                 = "snet-sql-${var.environment}-${var.location}-${local.instance}"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.vm_subnet_prefix]
}

resource "azurerm_subnet" "private_endpoints" {
  name                              = "snet-private-endpoints-${var.environment}-${var.location}-${local.instance}"
  resource_group_name               = azurerm_resource_group.this.name
  virtual_network_name              = azurerm_virtual_network.this.name
  address_prefixes                  = [var.private_endpoint_subnet_prefix]
  private_endpoint_network_policies = "Disabled"
}

resource "azurerm_subnet" "bastion" {
  name                 = "AzureBastionSubnet"
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.this.name
  address_prefixes     = [var.bastion_subnet_prefix]
}

resource "azurerm_network_interface" "sql" {
  name                = "nic-sql-${var.environment}-${var.location}-${local.instance}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = local.tags

  ip_configuration {
    name                          = "ipconfig"
    subnet_id                     = azurerm_subnet.sql.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_windows_virtual_machine" "sql" {
  name                = "vm-sql-${var.environment}-${local.instance}"
  computer_name       = "sql${var.environment}${local.instance}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  size                = var.vm_size
  admin_username      = var.admin_username
  admin_password      = var.admin_password
  network_interface_ids = [
    azurerm_network_interface.sql.id
  ]
  secure_boot_enabled = true
  vtpm_enabled        = true
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Premium_LRS"
  }

  source_image_reference {
    publisher = "MicrosoftSQLServer"
    offer     = "sql2022-ws2022"
    sku       = "sqldev-gen2"
    version   = "latest"
  }
}

resource "azurerm_mssql_virtual_machine" "sql" {
  virtual_machine_id = azurerm_windows_virtual_machine.sql.id
  sql_license_type   = "PAYG"
  tags               = local.tags
}

resource "azurerm_storage_account" "this" {
  name                            = local.storage_name
  resource_group_name             = azurerm_resource_group.this.name
  location                        = azurerm_resource_group.this.location
  account_tier                    = var.storage_account_tier
  account_replication_type        = var.storage_account_replication_type
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = false
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  https_traffic_only_enabled      = true
  tags                            = local.tags
}

resource "azurerm_private_dns_zone" "blob" {
  name                = "privatelink.blob.core.windows.net"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "blob" {
  name                  = "link-${azurerm_virtual_network.this.name}"
  resource_group_name   = azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.blob.name
  virtual_network_id    = azurerm_virtual_network.this.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_endpoint" "blob" {
  name                = "pep-${var.prefix}-blob-${var.environment}-${var.location}-${local.instance}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "blob-connection"
    private_connection_resource_id = azurerm_storage_account.this.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [azurerm_private_dns_zone.blob.id]
  }
}

resource "azurerm_role_assignment" "storage_blob_contributor" {
  scope                = azurerm_storage_account.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_windows_virtual_machine.sql.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_public_ip" "bastion" {
  name                = "pip-bastion-${local.resource_suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = local.tags
}

resource "azurerm_bastion_host" "this" {
  name                = "bas-${local.resource_suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Standard"
  tags                = local.tags

  ip_configuration {
    name                 = "bastionIpConfig"
    subnet_id            = azurerm_subnet.bastion.id
    public_ip_address_id = azurerm_public_ip.bastion.id
  }
}