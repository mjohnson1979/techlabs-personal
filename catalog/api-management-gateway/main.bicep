metadata description = 'API gateway landing zone: VNet-integrated API Management fronting a private App Service backend, with rate-limiting policy, App Insights logging, and a published product - a governed entry point for enterprise APIs.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'apigw'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('API Management SKU.')
@allowed([
  'Developer'
  'Premium'
])
param apimSku string = 'Developer'

@description('API Management capacity (scale units).')
@minValue(1)
param apimCapacity int = 1

@description('Publisher email for API Management.')
param apimPublisherEmail string = 'platform@contoso.com'

@description('Publisher name for API Management.')
param apimPublisherName string = 'Contoso Platform'

@description('Backend App Service plan SKU.')
@allowed([
  'P0v3'
  'P1v3'
  'P2v3'
])
param backendSku string = 'P1v3'

@description('Make the backend plan zone redundant.')
param zoneRedundant bool = false

@description('Requests-per-minute rate limit applied by the gateway product.')
@minValue(1)
param rateLimitCallsPerMinute int = 300

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var vnetAddressPrefix = '10.4.0.0/16'

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

resource apimNsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${namePrefix}-apim-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowApiManagementManagement'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'ApiManagement'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '3443'
        }
      }
      {
        name: 'AllowAzureLoadBalancer'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '6390'
        }
      }
      {
        name: 'AllowHttpsInbound'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

module network '../../modules/virtual-network.bicep' = {
  name: 'network'
  params: {
    name: '${namePrefix}-vnet'
    location: location
    tags: tags
    addressPrefixes: [
      vnetAddressPrefix
    ]
    defaultNsgId: apimNsg.id
    subnets: [
      {
        name: 'apim'
        prefix: '10.4.0.0/24'
      }
      {
        name: 'backend'
        prefix: '10.4.1.0/24'
        associateNsg: false
        delegations: [
          'Microsoft.Web/serverFarms'
        ]
      }
    ]
  }
}

var apimSubnetId = first(filter(network.outputs.subnets, s => s.name == 'apim')).id
var backendSubnetId = first(filter(network.outputs.subnets, s => s.name == 'backend')).id

resource backendPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-backend-plan'
  location: location
  tags: tags
  sku: {
    name: backendSku
    capacity: zoneRedundant ? 3 : 1
  }
  kind: 'linux'
  properties: {
    reserved: true
    zoneRedundant: zoneRedundant
  }
}

resource backendApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-backend'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: backendPlan.id
    httpsOnly: true
    virtualNetworkSubnetId: backendSubnetId
    vnetRouteAllEnabled: true
    publicNetworkAccess: 'Disabled'
    siteConfig: {
      linuxFxVersion: 'DOTNETCORE|8.0'
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/health'
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
      ]
    }
  }
}

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: '${namePrefix}-apim'
  location: location
  tags: tags
  sku: {
    name: apimSku
    capacity: apimCapacity
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: apimPublisherEmail
    publisherName: apimPublisherName
    virtualNetworkType: 'External'
    virtualNetworkConfiguration: {
      subnetResourceId: apimSubnetId
    }
  }
}

resource apimLogger 'Microsoft.ApiManagement/service/loggers@2023-09-01-preview' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsights.outputs.id
    credentials: {
      connectionString: appInsights.outputs.connectionString
    }
  }
}

resource backendApi 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'backend-api'
  properties: {
    displayName: 'Backend API'
    path: 'api'
    protocols: [
      'https'
    ]
    serviceUrl: 'https://${backendApp.properties.defaultHostName}'
    subscriptionRequired: true
  }
}

resource gatewayProduct 'Microsoft.ApiManagement/service/products@2023-09-01-preview' = {
  parent: apim
  name: 'standard'
  properties: {
    displayName: 'Standard'
    description: 'Rate-limited access to backend APIs.'
    subscriptionRequired: true
    approvalRequired: true
    state: 'published'
  }
}

resource productApiLink 'Microsoft.ApiManagement/service/products/apis@2023-09-01-preview' = {
  parent: gatewayProduct
  name: backendApi.name
}

resource productPolicy 'Microsoft.ApiManagement/service/products/policies@2023-09-01-preview' = {
  parent: gatewayProduct
  name: 'policy'
  properties: {
    format: 'xml'
    value: '<policies><inbound><rate-limit calls="${rateLimitCallsPerMinute}" renewal-period="60" /><base /></inbound><backend><base /></backend><outbound><base /></outbound><on-error><base /></on-error></policies>'
  }
}

output apimGatewayUrl string = apim.properties.gatewayUrl
output apimDeveloperPortalUrl string = apim.properties.developerPortalUrl
output backendHostName string = backendApp.properties.defaultHostName
