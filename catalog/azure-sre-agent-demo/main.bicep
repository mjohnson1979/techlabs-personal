metadata description = 'Deploys an isolated Azure Container Apps workload and observability foundation for Azure SRE Agent demonstrations.'

targetScope = 'resourceGroup'

@description('Azure region supported by Azure SRE Agent and Azure Container Apps.')
param location string = 'eastus2'

@description('Environment name used in resource names and tags.')
@minLength(2)
@maxLength(8)
param environment string = 'dev'

@description('Short workload name used in resource names.')
@minLength(3)
@maxLength(10)
param workloadName string = 'sredemo'

@description('Container image for the disposable demo workload.')
param containerImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Target port exposed by the container image.')
@minValue(1)
@maxValue(65535)
param targetPort int = 80

@description('Minimum replicas used to demonstrate idle overprovisioning and right-sizing.')
@minValue(1)
@maxValue(5)
param minReplicas int = 3

@description('Maximum replicas available to the demo workload.')
@minValue(1)
@maxValue(10)
param maxReplicas int = 5

@description('Microsoft Entra object ID of the group assigned the SRE Agent Administrator role.')
param sponsorGroupObjectId string = '5734b843-4ae2-4e07-8334-e3572f0b833d'

@description('AI model provider used by the SRE Agent.')
param agentModelProvider string = 'MicrosoftFoundry'

@description('AI model selection used by the SRE Agent.')
param agentModelName string = 'Automatic'

var instance = '001'
var resourceSuffix = '${workloadName}-${environment}-${location}-${instance}'
var logAnalyticsName = 'log-${resourceSuffix}'
var applicationInsightsName = 'appi-${resourceSuffix}'
var applicationInsightsResourceId = resourceId('Microsoft.Insights/components', applicationInsightsName)
var sreAgentName = 'sre-${workloadName}-${environment}-${instance}'
var containerAppsContributorRoleId = '358470bc-b998-42bd-ab17-a7e34c199c0f'
var monitoringReaderRoleId = '43d0d8ad-25c7-4714-9337-8ba259a9fe05'
var logAnalyticsReaderRoleId = '73c42c96-874c-492b-b04d-ab87d138a893'
var sreAgentAdministratorRoleId = 'e79298df-d852-4c6d-84f9-5d13249d1e55'
var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'azure-sre-agent-demo'
  Workload: workloadName
  Lifecycle: 'Temporary'
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

module applicationInsights '../../modules/app-insights/main.bicep' = {
  name: 'deploy-application-insights'
  params: {
    name: applicationInsightsName
    location: location
    tags: tags
    workspaceResourceId: logAnalytics.id
  }
}

resource sreAgentIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${workloadName}-sre-${environment}-${location}-${instance}'
  location: location
  tags: tags
}

resource containerAppsContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, sreAgentIdentity.id, containerAppsContributorRoleId)
  properties: {
    principalId: sreAgentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', containerAppsContributorRoleId)
  }
}

resource monitoringReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, sreAgentIdentity.id, monitoringReaderRoleId)
  properties: {
    principalId: sreAgentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringReaderRoleId)
  }
}

resource logAnalyticsReaderAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, sreAgentIdentity.id, logAnalyticsReaderRoleId)
  properties: {
    principalId: sreAgentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReaderRoleId)
  }
}

resource sreAgent 'Microsoft.App/agents@2026-01-01' = {
  name: sreAgentName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${sreAgentIdentity.id}': {}
    }
  }
  properties: {
    knowledgeGraphConfiguration: {
      identity: sreAgentIdentity.id
      managedResources: [
        resourceGroup().id
      ]
    }
    actionConfiguration: {
      identity: sreAgentIdentity.id
      mode: 'Autonomous'
      accessLevel: 'High'
    }
    logConfiguration: {
      applicationInsightsConfiguration: {
        appId: applicationInsights.outputs.appId
        connectionString: applicationInsights.outputs.connectionString
      }
    }
    incidentManagementConfiguration: {
      type: 'AzMonitor'
    }
    upgradeChannel: 'Stable'
    defaultModel: {
      provider: agentModelProvider
      name: agentModelName
    }
  }
  dependsOn: [
    containerAppsContributorAssignment
    monitoringReaderAssignment
    logAnalyticsReaderAssignment
  ]
}

resource sreAgentAdministratorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sreAgent.id, sponsorGroupObjectId, sreAgentAdministratorRoleId)
  scope: sreAgent
  properties: {
    principalId: sponsorGroupObjectId
    principalType: 'Group'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', sreAgentAdministratorRoleId)
  }
}

resource containerAppsEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${resourceSuffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${resourceSuffix}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: containerAppsEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
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
          name: 'demo'
          image: containerImage
          resources: {
            cpu: json('0.5')
            memory: '1.0Gi'
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

var availabilityTestName = 'webtest-${resourceSuffix}'
var availabilityTestId = guid(subscription().id, resourceGroup().id, availabilityTestName)

resource availabilityTest 'Microsoft.Insights/webtests@2022-06-15' = {
  name: availabilityTestName
  location: location
  tags: union(tags, {
    'hidden-link:${applicationInsightsResourceId}': 'Resource'
  })
  kind: 'ping'
  properties: {
    SyntheticMonitorId: availabilityTestId
    Name: availabilityTestName
    Description: 'Checks the SRE demo workload HTTPS endpoint.'
    Enabled: true
    Frequency: 300
    Timeout: 30
    Kind: 'ping'
    RetryEnabled: true
    Locations: [
      {
        Id: 'us-fl-mia-edge'
      }
      {
        Id: 'us-tx-sn1-azr'
      }
      {
        Id: 'us-ca-sjc-azr'
      }
    ]
    Configuration: {
      WebTest: '<WebTest Name="${availabilityTestName}" Id="${availabilityTestId}" Enabled="True" CssProjectStructure="" CssIteration="" Timeout="30" WorkItemIds="" xmlns="http://microsoft.com/schemas/VisualStudio/TeamTest/2010" Description="" CredentialUserName="" CredentialPassword="" PreAuthenticate="True" Proxy="default" StopOnError="False" RecordedResultFile="" ResultsLocale=""><Items><Request Method="GET" Guid="${guid(availabilityTestId, 'request')}" Version="1.1" Url="https://${containerApp.properties.configuration.ingress.fqdn}/" ThinkTime="0" Timeout="30" ParseDependentRequests="False" FollowRedirects="True" RecordResult="True" Cache="False" ResponseTimeGoal="0" Encoding="utf-8" ExpectedHttpStatusCode="200" ExpectedResponseUrl="" ReportingName="" IgnoreHttpStatusCode="False" /></Items></WebTest>'
    }
  }
}

resource availabilityAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: 'SRE demo availability failure'
  location: 'global'
  tags: tags
  properties: {
    description: 'Triggers an Azure SRE Agent investigation when the demo workload becomes unavailable.'
    severity: 2
    enabled: true
    scopes: [
      applicationInsights.outputs.id
      availabilityTest.id
    ]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria'
      webTestId: availabilityTest.id
      componentId: applicationInsights.outputs.id
      failedLocationCount: 2
    }
    autoMitigate: true
    targetResourceType: 'Microsoft.Insights/webtests'
    targetResourceRegion: location
    actions: []
  }
}

output containerAppName string = containerApp.name
output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppResourceId string = containerApp.id
output logAnalyticsWorkspaceResourceId string = logAnalytics.id
output applicationInsightsResourceId string = applicationInsights.outputs.id
output availabilityTestResourceId string = availabilityTest.id
output availabilityAlertResourceId string = availabilityAlert.id
output demoResourceGroupResourceId string = resourceGroup().id
output sreAgentResourceId string = sreAgent.id
output sreAgentName string = sreAgent.name
output sreAgentEndpoint string = sreAgent.properties.agentEndpoint
output sreAgentIdentityPrincipalId string = sreAgentIdentity.properties.principalId