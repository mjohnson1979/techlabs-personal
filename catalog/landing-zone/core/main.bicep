@description('Deployment location.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@description('Storage redundancy. Use Standard_ZRS or Standard_GRS in prod for resiliency.')
@allowed([
  'Standard_LRS'
  'Standard_ZRS'
  'Standard_GRS'
])
param storageSku string = 'Standard_LRS'

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
}

module logAnalytics '../../../modules/log-analytics.bicep' = {
  name: 'logAnalytics'
  params: {
    name: '${prefix}-law'
    location: location
    tags: tags
  }
}

module storage '../../../modules/storage-account.bicep' = {
  name: 'storage'
  params: {
    name: '${uniqueString(resourceGroup().id)}stg'
    location: location
    tags: tags
    skuName: storageSku
  }
}

module keyVault '../../../modules/key-vault.bicep' = {
  name: 'keyVault'
  params: {
    name: '${prefix}-kv'
    location: location
    tags: tags
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: '${prefix}-hub-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'default'
        properties: {
          addressPrefix: '10.0.1.0/24'
        }
      }
    ]
  }
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${prefix}-nsg'
  location: location
  tags: tags
}

output vnetName string = vnet.name
output nsgName string = nsg.name
output keyVaultName string = keyVault.outputs.name
output storageAccountName string = storage.outputs.name
output workspaceName string = logAnalytics.outputs.name
