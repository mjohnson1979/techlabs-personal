metadata description = 'Enterprise integration (iPaaS) on Logic Apps Standard with a Service Bus messaging backbone, keyless connectors via managed identity, and App Insights monitoring.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'intwf'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Logic Apps Standard plan SKU.')
@allowed([
  'WS1'
  'WS2'
  'WS3'
])
param workflowSku string = 'WS1'

@description('Service Bus SKU.')
@allowed([
  'Standard'
  'Premium'
])
param serviceBusSku string = 'Standard'

@description('Make the Service Bus namespace zone redundant (Premium only).')
param zoneRedundant bool = false

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var storageName = toLower('${namePrefix}wf')
var serviceBusDataOwnerRoleId = '090c5cfd-751d-490a-894a-3ce6f1109419'

module law '../../modules/log-analytics.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: location
    tags: tags
    retentionInDays: retentionInDays
  }
}

module appInsights '../../modules/app-insights.bicep' = {
  name: 'appInsights'
  params: {
    name: '${namePrefix}-ai'
    location: location
    tags: tags
    workspaceResourceId: law.outputs.id
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
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

resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${namePrefix}-sb'
  location: location
  tags: tags
  sku: {
    name: serviceBusSku
    tier: serviceBusSku
  }
  properties: {
    minimumTlsVersion: '1.2'
    disableLocalAuth: true
    zoneRedundant: serviceBusSku == 'Premium' ? zoneRedundant : false
  }
}

resource inboundQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: 'inbound'
  properties: {
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    lockDuration: 'PT1M'
  }
}

resource outboundQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: 'outbound'
  properties: {
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
  }
}

resource workflowPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
  location: location
  tags: tags
  sku: {
    name: workflowSku
    tier: 'WorkflowStandard'
  }
  kind: 'elastic'
  properties: {
    reserved: false
  }
}

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

resource logicApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-logic'
  location: location
  tags: tags
  kind: 'functionapp,workflowapp'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: workflowPlan.id
    httpsOnly: true
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'FUNCTIONS_WORKER_RUNTIME'
          value: 'node'
        }
        {
          name: 'APP_KIND'
          value: 'workflowApp'
        }
        {
          name: 'AzureWebJobsStorage'
          value: storageConnectionString
        }
        {
          name: 'WEBSITE_CONTENTAZUREFILECONNECTIONSTRING'
          value: storageConnectionString
        }
        {
          name: 'WEBSITE_CONTENTSHARE'
          value: toLower('${namePrefix}-logic')
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'serviceBus__fullyQualifiedNamespace'
          value: '${serviceBus.name}.servicebus.windows.net'
        }
      ]
    }
  }
}

// Logic App identity can send/receive on Service Bus (keyless connector)
resource sbRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: serviceBus
  name: guid(serviceBus.id, logicApp.id, serviceBusDataOwnerRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataOwnerRoleId)
    principalId: logicApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output logicAppName string = logicApp.name
output logicAppHostName string = logicApp.properties.defaultHostName
output serviceBusNamespace string = '${serviceBus.name}.servicebus.windows.net'
