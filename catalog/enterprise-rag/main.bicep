metadata description = 'Enterprise RAG platform: Azure OpenAI (chat + embeddings), Azure AI Search, Cosmos DB chat history, a blob document store, an App Service orchestrator, and API Management - fully keyless via managed identity.'

@description('Prefix for resource names.')
param namePrefix string = 'entrag'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Chat completion model to deploy.')
param chatModel string = 'gpt-4.1-mini'

@description('Chat model version.')
param chatModelVersion string = '2025-04-14'

@description('Chat deployment capacity (thousands of TPM).')
@minValue(1)
param chatCapacity int = 20

@description('Embedding model to deploy.')
param embeddingModel string = 'text-embedding-3-large'

@description('Embedding model version.')
param embeddingModelVersion string = '1'

@description('Azure AI Search SKU.')
@allowed([
  'basic'
  'standard'
  'standard2'
])
param searchSku string = 'standard'

@description('Number of Azure AI Search replicas.')
@minValue(1)
param searchReplicas int = 1

@description('App Service plan SKU.')
@allowed([
  'P0v3'
  'P1v3'
  'P2v3'
])
param appServiceSku string = 'P1v3'

@description('Make the App Service plan zone redundant.')
param zoneRedundant bool = false

@description('API Management SKU.')
@allowed([
  'Developer'
  'Standard'
  'Premium'
])
param apimSku string = 'Developer'

@description('Publisher email for API Management.')
param apimPublisherEmail string = 'platform@contoso.com'

@description('Publisher name for API Management.')
param apimPublisherName string = 'Contoso Platform'

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var openAiUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

var documentsStorageName = toLower('${namePrefix}docs')

module law '../../modules/log-analytics/main.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: location
    tags: tags
    retentionInDays: retentionInDays
  }
}

module appInsights '../../modules/app-insights/main.bicep' = {
  name: 'appInsights'
  params: {
    name: '${namePrefix}-ai'
    location: location
    tags: tags
    workspaceResourceId: law.outputs.id
  }
}

module documents '../../modules/storage-account/main.bicep' = {
  name: 'documents'
  params: {
    name: documentsStorageName
    location: location
    tags: tags
    skuName: zoneRedundant ? 'Standard_ZRS' : 'Standard_LRS'
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${namePrefix}-openai'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: '${namePrefix}-openai'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: chatModel
  sku: {
    name: 'Standard'
    capacity: chatCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModel
      version: chatModelVersion
    }
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: embeddingModel
  sku: {
    name: 'Standard'
    capacity: 50
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: embeddingModel
      version: embeddingModelVersion
    }
  }
  dependsOn: [
    chatDeployment
  ]
}

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: '${namePrefix}-search'
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
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'enabled'
    disableLocalAuth: true
    authOptions: null
    semanticSearch: 'standard'
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: toLower('${namePrefix}-cosmos')
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    enableAutomaticFailover: zoneRedundant
    minimalTlsVersion: 'Tls12'
    capabilities: [
      {
        name: 'EnableServerless'
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: zoneRedundant
      }
    ]
  }
}

resource cosmosDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: 'chat'
  properties: {
    resource: {
      id: 'chat'
    }
  }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'history'
  properties: {
    resource: {
      id: 'history'
      partitionKey: {
        paths: [
          '/sessionId'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
  location: location
  tags: tags
  sku: {
    name: appServiceSku
    capacity: zoneRedundant ? 3 : 1
  }
  kind: 'linux'
  properties: {
    reserved: true
    zoneRedundant: zoneRedundant
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-app'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/health'
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAi.properties.endpoint
        }
        {
          name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
          value: chatModel
        }
        {
          name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
          value: embeddingModel
        }
        {
          name: 'AZURE_SEARCH_ENDPOINT'
          value: 'https://${search.name}.search.windows.net'
        }
        {
          name: 'AZURE_COSMOS_ENDPOINT'
          value: cosmos.properties.documentEndpoint
        }
        {
          name: 'AZURE_STORAGE_ACCOUNT'
          value: documents.outputs.name
        }
      ]
    }
  }
}

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: '${namePrefix}-apim'
  location: location
  tags: tags
  sku: {
    name: apimSku
    capacity: apimSku == 'Developer' ? 1 : 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
  }
}

resource ragApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'rag-api'
  properties: {
    displayName: 'RAG Chat API'
    path: 'chat'
    protocols: [
      'https'
    ]
    serviceUrl: 'https://${webApp.properties.defaultHostName}'
    subscriptionRequired: true
  }
}

// App identity can call Azure OpenAI
resource appOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, webApp.id, openAiUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Search identity can call Azure OpenAI for integrated vectorization
resource searchOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, search.id, openAiUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRoleId)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// App identity can query the search index
resource appSearchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, webApp.id, searchIndexDataReaderRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// App identity can manage indexes / indexers
resource appSearchContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, webApp.id, searchServiceContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalId: webApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Search identity can read source documents from blob storage
resource searchStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageForRole
  name: guid(storageForRole.id, search.id, storageBlobDataReaderRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    documents
  ]
}

resource storageForRole 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: documentsStorageName
}

// App identity can read/write Cosmos data plane
resource appCosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, webApp.id, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: webApp.identity.principalId
    scope: cosmos.id
  }
}

output orchestratorHostName string = webApp.properties.defaultHostName
output apimGatewayUrl string = apim.properties.gatewayUrl
output openAiEndpoint string = openAi.properties.endpoint
output searchEndpoint string = 'https://${search.name}.search.windows.net'
