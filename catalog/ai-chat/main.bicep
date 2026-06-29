@description('Deployment location. Azure OpenAI model availability varies by region.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@description('App Service plan SKU (P1v3+ for zone redundancy).')
@allowed([
  'B1'
  'S1'
  'P0v3'
  'P1v3'
])
param appServiceSku string = 'B1'

@description('App Service plan instance count.')
@minValue(1)
@maxValue(30)
param appCapacity int = 1

@description('Zone-redundant App Service plan (requires Premium v3 and capacity >= 3).')
param appZoneRedundant bool = false

@description('Chat model to deploy.')
param chatModelName string = 'gpt-5-mini'

@description('Chat model version.')
param chatModelVersion string = '2025-08-07'

@description('Model deployment SKU. GlobalStandard is the generally-available tier for current GPT-5 models.')
param chatModelSku string = 'GlobalStandard'

@description('Tokens-per-minute capacity (thousands) for the model deployment.')
@minValue(1)
@maxValue(200)
param chatModelCapacity int = 10

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'ai-chat'
}

module logAnalytics '../../modules/log-analytics/main.bicep' = {
  name: 'logAnalytics'
  params: {
    name: '${prefix}-${environment}-law'
    location: location
    tags: tags
  }
}

module appInsights '../../modules/app-insights/main.bicep' = {
  name: 'appInsights'
  params: {
    name: '${prefix}-${environment}-ai'
    location: location
    tags: tags
    workspaceResourceId: logAnalytics.outputs.id
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: '${prefix}-${environment}-openai-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: '${prefix}-${environment}-openai-${uniqueString(resourceGroup().id)}'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: openAi
  name: chatModelName
  sku: {
    name: chatModelSku
    capacity: chatModelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: chatModelName
      version: chatModelVersion
    }
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: '${prefix}-${environment}-cosmos-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    minimalTlsVersion: 'Tls12'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      {
        name: 'EnableServerless'
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
  name: 'conversations'
  properties: {
    resource: {
      id: 'conversations'
      partitionKey: {
        paths: [
          '/sessionId'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-${environment}-plan'
  location: location
  tags: tags
  sku: {
    name: appServiceSku
    capacity: appCapacity
  }
  kind: 'linux'
  properties: {
    reserved: true
    zoneRedundant: appZoneRedundant
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${prefix}-${environment}-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOTNETCORE|8.0'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      http20Enabled: true
      healthCheckPath: '/health'
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'ApplicationInsightsAgent_EXTENSION_VERSION'
          value: '~3'
        }
        {
          name: 'AZURE_OPENAI_ENDPOINT'
          value: openAi.properties.endpoint
        }
        {
          name: 'AZURE_OPENAI_DEPLOYMENT'
          value: chatDeployment.name
        }
        {
          name: 'COSMOS_ENDPOINT'
          value: cosmos.properties.documentEndpoint
        }
        {
          name: 'COSMOS_DATABASE'
          value: cosmosDb.name
        }
        {
          name: 'COSMOS_CONTAINER'
          value: cosmosContainer.name
        }
      ]
    }
  }
}

output webAppName string = webApp.name
output webAppHostname string = webApp.properties.defaultHostName
output webAppPrincipalId string = webApp.identity.principalId
output openAiEndpoint string = openAi.properties.endpoint
output cosmosAccountName string = cosmos.name
output workspaceName string = logAnalytics.outputs.name
