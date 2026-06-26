@description('Name of the storage account (3-24 lowercase alphanumeric characters).')
@minLength(3)
@maxLength(24)
param name string

@description('Azure region for the storage account.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Storage account SKU.')
@allowed([
  'Standard_LRS'
  'Standard_GRS'
  'Standard_ZRS'
  'Premium_LRS'
])
param skuName string = 'Standard_LRS'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

output id string = storage.id
output name string = storage.name
