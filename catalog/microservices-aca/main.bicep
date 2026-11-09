metadata description = 'Event-driven microservices on Azure Container Apps with Dapr: a Service Bus pub/sub backbone, a Cosmos DB Dapr state store, an external API service and an internal worker, all keyless via a shared workload identity.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'acamesh'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Container image for the API service.')
param apiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Container image for the worker service.')
param workerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Minimum replicas per app.')
@minValue(0)
param minReplicas int = 0

@description('Maximum replicas per app.')
@minValue(1)
param maxReplicas int = 10

@description('Make the Container Apps environment zone redundant.')
param zoneRedundant bool = false

@description('Service Bus SKU.')
@allowed([
  'Standard'
  'Premium'
])
param serviceBusSku string = 'Standard'

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var serviceBusDataOwnerRoleId = '090c5cfd-751d-490a-894a-3ce6f1109419'
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

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

resource lawResource 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: '${namePrefix}-law'
}

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-id'
  location: location
  tags: tags
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

resource ordersTopic 'Microsoft.ServiceBus/namespaces/topics@2022-10-01-preview' = {
  parent: serviceBus
  name: 'orders'
  properties: {
    maxSizeInMegabytes: 1024
  }
}

resource ordersSubscription 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2022-10-01-preview' = {
  parent: ordersTopic
  name: 'worker'
  properties: {
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
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
  name: 'state'
  properties: {
    resource: {
      id: 'state'
    }
  }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: cosmosDb
  name: 'state'
  properties: {
    resource: {
      id: 'state'
      partitionKey: {
        paths: [
          '/partitionKey'
        ]
        kind: 'Hash'
      }
    }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.outputs.customerId
        sharedKey: lawResource.listKeys().primarySharedKey
      }
    }
    zoneRedundant: zoneRedundant
    daprAIConnectionString: appInsights.outputs.connectionString
  }
}

resource daprStateStore 'Microsoft.App/managedEnvironments/daprComponents@2024-03-01' = {
  parent: environment
  name: 'statestore'
  properties: {
    componentType: 'state.azure.cosmosdb'
    version: 'v1'
    metadata: [
      {
        name: 'url'
        value: cosmos.properties.documentEndpoint
      }
      {
        name: 'database'
        value: 'state'
      }
      {
        name: 'collection'
        value: 'state'
      }
      {
        name: 'azureClientId'
        value: appIdentity.properties.clientId
      }
    ]
    scopes: [
      'api'
      'worker'
    ]
  }
}

resource daprPubSub 'Microsoft.App/managedEnvironments/daprComponents@2024-03-01' = {
  parent: environment
  name: 'orderpubsub'
  properties: {
    componentType: 'pubsub.azure.servicebus.topics'
    version: 'v1'
    metadata: [
      {
        name: 'namespaceName'
        value: '${serviceBus.name}.servicebus.windows.net'
      }
      {
        name: 'azureClientId'
        value: appIdentity.properties.clientId
      }
    ]
    scopes: [
      'api'
      'worker'
    ]
  }
}

resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-api'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      dapr: {
        enabled: true
        appId: 'api'
        appProtocol: 'http'
        appPort: 8080
      }
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-rule'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${namePrefix}-worker'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      dapr: {
        enabled: true
        appId: 'worker'
        appProtocol: 'http'
        appPort: 8080
      }
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: workerImage
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: minReplicas == 0 ? 1 : minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'servicebus-rule'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                topicName: 'orders'
                subscriptionName: 'worker'
                messageCount: '20'
                namespace: '${serviceBus.name}.servicebus.windows.net'
              }
              #disable-next-line BCP037
              identity: appIdentity.id
            }
          }
        ]
      }
    }
  }
}

resource sbRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: serviceBus
  name: guid(serviceBus.id, appIdentity.id, serviceBusDataOwnerRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', serviceBusDataOwnerRoleId)
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource cosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, appIdentity.id, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: appIdentity.properties.principalId
    scope: cosmos.id
  }
}

output apiFqdn string = apiApp.properties.configuration.ingress.fqdn
output environmentName string = environment.name
output serviceBusNamespace string = '${serviceBus.name}.servicebus.windows.net'
output cosmosEndpoint string = cosmos.properties.documentEndpoint
