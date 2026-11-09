metadata description = 'Real-time telemetry pipeline: Event Hubs ingestion, a Stream Analytics job shaping events to a curated lake, a Functions consumer for the hot path, and Cosmos DB as the serving store - all keyless via managed identity.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'stream'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Event Hubs namespace SKU.')
@allowed([
  'Standard'
  'Premium'
])
param eventHubsSku string = 'Standard'

@description('Throughput units (Standard) for the Event Hubs namespace.')
@minValue(1)
param throughputUnits int = 1

@description('Number of partitions on the event hub.')
@minValue(2)
param partitionCount int = 4

@description('Message retention in days for the event hub.')
@minValue(1)
param messageRetentionDays int = 1

@description('Stream Analytics streaming units.')
@minValue(1)
param streamingUnits int = 3

@description('Function plan SKU (EP1/EP2 elastic premium, or Y1 consumption).')
@allowed([
  'Y1'
  'EP1'
  'EP2'
])
param functionPlanSku string = 'EP1'

@description('Make storage and Cosmos zone redundant.')
param zoneRedundant bool = false

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var eventHubDataReceiverRoleId = 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'
var lakeStorageName = toLower('${namePrefix}lake')
var funcStorageName = toLower('${namePrefix}func')

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

module lake '../../modules/storage-account/main.bicep' = {
  name: 'lake'
  params: {
    name: lakeStorageName
    location: location
    tags: tags
    skuName: zoneRedundant ? 'Standard_ZRS' : 'Standard_LRS'
  }
}

resource lakeAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: lakeStorageName
}

resource lakeBlob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: lakeAccount
  name: 'default'
}

resource curatedContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: lakeBlob
  name: 'curated'
  dependsOn: [
    lake
  ]
}

resource funcStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: funcStorageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource eventHubNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: '${namePrefix}-ehns'
  location: location
  tags: tags
  sku: {
    name: eventHubsSku
    tier: eventHubsSku
    capacity: throughputUnits
  }
  properties: {
    minimumTlsVersion: '1.2'
    disableLocalAuth: true
    zoneRedundant: zoneRedundant
    isAutoInflateEnabled: eventHubsSku == 'Standard'
    maximumThroughputUnits: eventHubsSku == 'Standard' ? 10 : 0
  }
}

resource eventHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: eventHubNamespace
  name: 'telemetry'
  properties: {
    partitionCount: partitionCount
    messageRetentionInDays: messageRetentionDays
  }
}

resource consumerGroupAnalytics 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: eventHub
  name: 'analytics'
}

resource consumerGroupFunctions 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: eventHub
  name: 'functions'
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: toLower('${namePrefix}-cosmos')
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
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
  name: 'telemetry'
  properties: {
    resource: {
      id: 'telemetry'
    }
  }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'events'
  properties: {
    resource: {
      id: 'events'
      partitionKey: {
        paths: [
          '/deviceId'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource streamingJob 'Microsoft.StreamAnalytics/streamingjobs@2021-10-01-preview' = {
  name: '${namePrefix}-asa'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    sku: {
      name: 'Standard'
    }
    jobType: 'Cloud'
    outputErrorPolicy: 'Stop'
    eventsOutOfOrderPolicy: 'Adjust'
    eventsOutOfOrderMaxDelayInSeconds: 5
    eventsLateArrivalMaxDelayInSeconds: 16
    dataLocale: 'en-US'
    compatibilityLevel: '1.2'
  }
}

resource asaInput 'Microsoft.StreamAnalytics/streamingjobs/inputs@2021-10-01-preview' = {
  parent: streamingJob
  name: 'eventhub-input'
  properties: {
    type: 'Stream'
    datasource: {
      type: 'Microsoft.EventHub/EventHub'
      properties: {
        serviceBusNamespace: eventHubNamespace.name
        eventHubName: eventHub.name
        consumerGroupName: consumerGroupAnalytics.name
        authenticationMode: 'Msi'
      }
    }
    serialization: {
      type: 'Json'
      properties: {
        encoding: 'UTF8'
      }
    }
  }
}

resource asaOutput 'Microsoft.StreamAnalytics/streamingjobs/outputs@2021-10-01-preview' = {
  parent: streamingJob
  name: 'lake-output'
  properties: {
    datasource: {
      type: 'Microsoft.Storage/Blob'
      properties: {
        storageAccounts: [
          {
            accountName: lakeStorageName
          }
        ]
        container: 'curated'
        pathPattern: 'telemetry/{date}/{time}'
        dateFormat: 'yyyy/MM/dd'
        timeFormat: 'HH'
        authenticationMode: 'Msi'
      }
    }
    serialization: {
      type: 'Parquet'
      properties: {}
    }
  }
  dependsOn: [
    curatedContainer
  ]
}

resource asaTransformation 'Microsoft.StreamAnalytics/streamingjobs/transformations@2021-10-01-preview' = {
  parent: streamingJob
  name: 'Transformation'
  properties: {
    streamingUnits: streamingUnits
    query: 'SELECT deviceId, eventType, value, System.Timestamp() AS windowEnd INTO [lake-output] FROM [eventhub-input] TIMESTAMP BY eventTime'
  }
}

resource functionPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-func-plan'
  location: location
  tags: tags
  sku: {
    name: functionPlanSku
    tier: functionPlanSku == 'Y1' ? 'Dynamic' : 'ElasticPremium'
  }
  kind: 'elastic'
  properties: {
    reserved: true
    maximumElasticWorkerCount: functionPlanSku == 'Y1' ? null : 10
  }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-func'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'DOTNET-ISOLATED|8.0'
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'dotnet-isolated'
        }
        {
          name: 'AzureWebJobsStorage__accountName'
          value: funcStorageName
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'EventHubConnection__fullyQualifiedNamespace'
          value: '${eventHubNamespace.name}.servicebus.windows.net'
        }
        {
          name: 'EventHubName'
          value: eventHub.name
        }
        {
          name: 'CosmosEndpoint'
          value: cosmos.properties.documentEndpoint
        }
      ]
    }
  }
}

// Stream Analytics can receive from Event Hubs
resource asaEventHubRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: eventHubNamespace
  name: guid(eventHubNamespace.id, streamingJob.id, eventHubDataReceiverRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventHubDataReceiverRoleId)
    principalId: streamingJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Stream Analytics can write curated parquet to the lake
resource asaStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: lakeAccount
  name: guid(lakeAccount.id, streamingJob.id, storageBlobDataContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: streamingJob.identity.principalId
    principalType: 'ServicePrincipal'
  }
  dependsOn: [
    lake
  ]
}

// Function can receive from Event Hubs
resource funcEventHubRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: eventHubNamespace
  name: guid(eventHubNamespace.id, functionApp.id, eventHubDataReceiverRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventHubDataReceiverRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function host storage access
resource funcStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: funcStorage
  name: guid(funcStorage.id, functionApp.id, storageBlobDataContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function can write to the Cosmos serving store
resource funcCosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, functionApp.id, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: functionApp.identity.principalId
    scope: cosmos.id
  }
}

output eventHubNamespaceFqdn string = '${eventHubNamespace.name}.servicebus.windows.net'
output eventHubName string = eventHub.name
output streamingJobName string = streamingJob.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output lakeAccountName string = lakeStorageName
