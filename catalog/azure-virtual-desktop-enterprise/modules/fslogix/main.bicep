targetScope = 'resourceGroup'

@description('Storage account name for FSLogix profiles (3-24 lowercase alphanumeric).')
@minLength(3)
@maxLength(24)
param storageAccountName string

@description('Azure region for FSLogix storage.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Name of the Azure Files share used for profile containers.')
param profileShareName string = 'profiles'

resource fslogixStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    allowSharedKeyAccess: true
    encryption: {
      keySource: 'Microsoft.Storage'
      services: {
        file: {
          enabled: true
        }
        blob: {
          enabled: true
        }
      }
    }
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: fslogixStorage
  name: 'default'
}

resource profileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: profileShareName
  properties: {
    enabledProtocols: 'SMB'
    accessTier: 'TransactionOptimized'
  }
}

output storageAccountId string = fslogixStorage.id
output storageAccountName string = fslogixStorage.name
output profileShareResourceId string = profileShare.id
