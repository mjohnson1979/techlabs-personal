@description('Deployment location.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@description('Container image to run.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('vCPU allocated per replica.')
@allowed([
  '0.25'
  '0.5'
  '0.75'
  '1.0'
  '2.0'
])
param cpu string = '0.5'

@description('Memory allocated per replica (e.g. 1.0Gi).')
param memory string = '1.0Gi'

@description('Minimum replicas. Set to 0 for scale-to-zero (dev), 1+ to avoid cold starts (prod).')
@minValue(0)
@maxValue(30)
param minReplicas int = 0

@description('Maximum replicas the app can scale out to.')
@minValue(1)
@maxValue(30)
param maxReplicas int = 5

@description('Make the Container Apps environment zone redundant (prod reliability).')
param zoneRedundant bool = false

@description('Target port the container listens on.')
param targetPort int = 80

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'container-app'
}

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-${environment}-law'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource environmentRes 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-${environment}-cae'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    zoneRedundant: zoneRedundant
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-${environment}-app'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environmentRes.id
    configuration: {
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }
    }
    template: {
      containers: [
        {
          name: 'app'
          image: containerImage
          resources: {
            cpu: json(cpu)
            memory: memory
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scale'
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

output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output environmentName string = environmentRes.name
output workspaceName string = law.name
