targetScope = 'resourceGroup'

@description('Prefix used for monitoring resource names.')
param namePrefix string = 'avd'

@description('Azure region for monitoring resources.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Log Analytics workspace retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

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

output logAnalyticsWorkspaceId string = law.outputs.id
output logAnalyticsWorkspaceName string = law.outputs.name
output appInsightsId string = appInsights.outputs.id
output appInsightsConnectionString string = appInsights.outputs.connectionString
