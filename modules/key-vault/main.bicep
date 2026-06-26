@description('Name of the Key Vault.')
param name string

@description('Azure region for the Key Vault.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Enable Azure RBAC for data-plane authorization.')
param enableRbacAuthorization bool = true

resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    enableRbacAuthorization: enableRbacAuthorization
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

output id string = keyVault.id
output name string = keyVault.name
