@description('Deployment location. AI model availability varies by region.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@description('Storage redundancy. Use Standard_ZRS or Standard_GRS in prod.')
@allowed([
  'Standard_LRS'
  'Standard_ZRS'
  'Standard_GRS'
])
param storageSku string = 'Standard_LRS'

@description('Azure AI Search SKU. Use standard (or higher) in prod for SLA + scale.')
@allowed([
  'basic'
  'standard'
  'standard2'
])
param searchSku string = 'basic'

@description('Number of search replicas. Use >= 3 in prod for query + indexing SLA.')
@minValue(1)
@maxValue(12)
param searchReplicas int = 1

@description('Number of search partitions.')
@minValue(1)
@maxValue(12)
param searchPartitions int = 1

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'ai-landing-zone'
}

module logAnalytics '../../../modules/log-analytics.bicep' = {
  name: 'logAnalytics'
  params: {
    name: '${prefix}-${environment}-ai-law'
    location: location
    tags: tags
  }
}

module keyVault '../../../modules/key-vault.bicep' = {
  name: 'keyVault'
  params: {
    name: '${prefix}-${environment}-aikv'
    location: location
    tags: tags
  }
}

module storage '../../../modules/storage-account.bicep' = {
  name: 'storage'
  params: {
    name: '${uniqueString(resourceGroup().id)}aistg'
    location: location
    tags: tags
    skuName: storageSku
  }
}

resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${prefix}-${environment}-aifoundry-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: '${prefix}-${environment}-aifoundry-${uniqueString(resourceGroup().id)}'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: '${prefix}-${environment}-search-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  sku: {
    name: searchSku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: searchReplicas
    partitionCount: searchPartitions
    authOptions: null
    disableLocalAuth: true
    publicNetworkAccess: 'enabled'
    semanticSearch: 'free'
  }
}

output workspaceName string = logAnalytics.outputs.name
output keyVaultName string = keyVault.outputs.name
output storageAccountName string = storage.outputs.name
output aiServicesName string = aiServices.name
output aiServicesEndpoint string = aiServices.properties.endpoint
output searchName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'
