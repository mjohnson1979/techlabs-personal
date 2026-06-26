@description('Deployment location.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@description('App Service plan SKU (P1v3+ to allow zone redundancy).')
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

@description('Azure SQL database SKU name (e.g. GP_Gen5_2 for General Purpose).')
param sqlSkuName string = 'GP_S_Gen5_1'

@description('Azure SQL database tier (e.g. GeneralPurpose, BusinessCritical).')
param sqlTier string = 'GeneralPurpose'

@description('Make the SQL database zone redundant (prod reliability).')
param sqlZoneRedundant bool = false

@description('Microsoft Entra admin object id (user/group) for the SQL server. Required for Entra-only auth.')
param sqlAdminObjectId string

@description('Microsoft Entra admin display name for the SQL server.')
param sqlAdminLogin string

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'sql-data-api'
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

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: '${prefix}-${environment}-sql-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  properties: {
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      principalType: 'User'
      login: sqlAdminLogin
      sid: sqlAdminObjectId
      tenantId: tenant().tenantId
      azureADOnlyAuthentication: true
    }
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: '${prefix}db'
  location: location
  tags: tags
  sku: {
    name: sqlSkuName
    tier: sqlTier
  }
  properties: {
    zoneRedundant: sqlZoneRedundant
  }
}

resource sqlAllowAzure 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAllWindowsAzureIps'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
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
          // Passwordless connection: the web app's managed identity authenticates to SQL.
          name: 'ConnectionStrings__Default'
          value: 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Database=${sqlDatabase.name};Authentication=Active Directory Default;Encrypt=True;'
        }
      ]
    }
  }
}

output webAppName string = webApp.name
output webAppHostname string = webApp.properties.defaultHostName
output webAppPrincipalId string = webApp.identity.principalId
output sqlServerName string = sqlServer.name
output sqlDatabaseName string = sqlDatabase.name
output workspaceName string = logAnalytics.outputs.name
