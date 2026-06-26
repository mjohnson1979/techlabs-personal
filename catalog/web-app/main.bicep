@description('Deployment location.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@description('App Service plan SKU. Use a Premium v3 SKU (P1v3+) to enable zone redundancy.')
@allowed([
  'B1'
  'B2'
  'S1'
  'P0v3'
  'P1v3'
  'P2v3'
])
param sku string = 'B1'

@description('Number of plan instances. Zone redundancy requires at least 3 on a Premium v3 plan.')
@minValue(1)
@maxValue(30)
param capacity int = 1

@description('Spread instances across availability zones (reliability). Requires a Premium v3 SKU and capacity >= 3.')
param zoneRedundant bool = false

@description('Linux runtime stack for the web app (e.g. DOTNETCORE|8.0, NODE|20-lts, PYTHON|3.12).')
param linuxFxVersion string = 'DOTNETCORE|8.0'

@description('Relative health check path used by the platform to evict unhealthy instances.')
param healthCheckPath string = '/health'

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'web-app'
}

module logAnalytics '../../modules/log-analytics.bicep' = {
  name: 'logAnalytics'
  params: {
    name: '${prefix}-${environment}-law'
    location: location
    tags: tags
  }
}

module appInsights '../../modules/app-insights.bicep' = {
  name: 'appInsights'
  params: {
    name: '${prefix}-${environment}-ai'
    location: location
    tags: tags
    workspaceResourceId: logAnalytics.outputs.id
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${prefix}-${environment}-plan'
  location: location
  tags: tags
  sku: {
    name: sku
    capacity: capacity
  }
  kind: 'linux'
  properties: {
    reserved: true
    zoneRedundant: zoneRedundant
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
      linuxFxVersion: linuxFxVersion
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      http20Enabled: true
      healthCheckPath: healthCheckPath
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'ApplicationInsightsAgent_EXTENSION_VERSION'
          value: '~3'
        }
      ]
    }
  }
}

output webAppName string = webApp.name
output webAppHostname string = webApp.properties.defaultHostName
output webAppPrincipalId string = webApp.identity.principalId
output appInsightsName string = appInsights.outputs.name
output workspaceName string = logAnalytics.outputs.name
