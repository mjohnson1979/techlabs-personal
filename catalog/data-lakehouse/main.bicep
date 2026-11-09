metadata description = 'Medallion data lakehouse: ADLS Gen2 with bronze/silver/gold zones, Data Factory with a managed VNet for ingestion/orchestration, a secure Databricks workspace for transformation, and Key Vault for secrets.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'lakehs'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Storage redundancy for the data lake.')
@allowed([
  'Standard_LRS'
  'Standard_ZRS'
  'Standard_GRS'
])
param lakeStorageSku string = 'Standard_LRS'

@description('Databricks workspace SKU.')
@allowed([
  'standard'
  'premium'
])
param databricksSku string = 'premium'

@description('Deploy Databricks with no public IP (secure cluster connectivity).')
param databricksNoPublicIp bool = true

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var lakeName = toLower('${namePrefix}lake')
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var managedResourceGroupName = '${namePrefix}-dbw-managed'

module law '../../modules/log-analytics/main.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: location
    tags: tags
    retentionInDays: retentionInDays
  }
}

module keyVault '../../modules/key-vault/main.bicep' = {
  name: 'keyVault'
  params: {
    name: '${namePrefix}-kv'
    location: location
    tags: tags
  }
}

resource lake 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: lakeName
  location: location
  tags: tags
  sku: {
    name: lakeStorageSku
  }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: false
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource lakeBlob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: lake
  name: 'default'
}

resource bronze 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: lakeBlob
  name: 'bronze'
}

resource silver 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: lakeBlob
  name: 'silver'
}

resource gold 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: lakeBlob
  name: 'gold'
}

resource dataFactory 'Microsoft.DataFactory/factories@2018-06-01' = {
  name: '${namePrefix}-adf'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource adfManagedVnet 'Microsoft.DataFactory/factories/managedVirtualNetworks@2018-06-01' = {
  parent: dataFactory
  name: 'default'
  properties: {}
}

resource adfIntegrationRuntime 'Microsoft.DataFactory/factories/integrationRuntimes@2018-06-01' = {
  parent: dataFactory
  name: 'AutoResolveIntegrationRuntime'
  properties: {
    type: 'Managed'
    managedVirtualNetwork: {
      type: 'ManagedVirtualNetworkReference'
      referenceName: 'default'
    }
    typeProperties: {
      computeProperties: {
        location: 'AutoResolve'
      }
    }
  }
  dependsOn: [
    adfManagedVnet
  ]
}

resource databricks 'Microsoft.Databricks/workspaces@2024-05-01' = {
  name: '${namePrefix}-dbw'
  location: location
  tags: tags
  sku: {
    name: databricksSku
  }
  properties: {
    managedResourceGroupId: subscriptionResourceId('Microsoft.Resources/resourceGroups', managedResourceGroupName)
    parameters: {
      enableNoPublicIp: {
        value: databricksNoPublicIp
      }
    }
  }
}

resource adfDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: dataFactory
  name: 'adf-diagnostics'
  properties: {
    workspaceId: law.outputs.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// Data Factory can read/write the data lake
resource adfLakeRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: lake
  name: guid(lake.id, dataFactory.id, storageBlobDataContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: dataFactory.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output lakeAccountName string = lake.name
output dataFactoryName string = dataFactory.name
output databricksWorkspaceUrl string = databricks.properties.workspaceUrl
output keyVaultName string = keyVault.outputs.name
