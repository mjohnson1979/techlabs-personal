targetScope = 'resourceGroup'

@description('Prefix used for monitoring resource names.')
param namePrefix string = 'avs'

@description('Azure region for monitoring resources.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Log Analytics retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

@description('Optional AVS private cloud resource name for diagnostics. Leave empty to skip AVS diagnostics.')
param avsPrivateCloudName string = ''

resource avsPrivateCloud 'Microsoft.AVS/privateClouds@2023-09-01' existing = if (!empty(avsPrivateCloudName)) {
  name: avsPrivateCloudName
}

module law '../../../../modules/log-analytics/main.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: location
    tags: tags
    retentionInDays: retentionInDays
  }
}

module appInsights '../../../../modules/app-insights/main.bicep' = {
  name: 'appInsights'
  params: {
    name: '${namePrefix}-appi'
    location: location
    tags: tags
    workspaceResourceId: law.outputs.id
  }
}

resource avsDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(avsPrivateCloudName)) {
  scope: avsPrivateCloud
  name: 'avs-diagnostics'
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

output logAnalyticsWorkspaceId string = law.outputs.id
output logAnalyticsWorkspaceName string = law.outputs.name
output appInsightsId string = appInsights.outputs.id
