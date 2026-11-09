metadata description = 'Network-hardened App Service baseline: private SQL and Key Vault behind private endpoints, App Service VNet integration, and an Application Gateway with WAF as the only public entry point.'

@description('Prefix for resource names.')
param namePrefix string = 'securewa'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('App Service plan SKU.')
@allowed([
  'P0v3'
  'P1v3'
  'P2v3'
])
param appServiceSku string = 'P1v3'

@description('Number of App Service plan instances.')
@minValue(1)
param appServiceCapacity int = 1

@description('Make the App Service plan zone redundant.')
param zoneRedundant bool = false

@description('Azure SQL database SKU name.')
param sqlSkuName string = 'GP_S_Gen5_1'

@description('Entra ID object ID of the SQL admin (user or group).')
param sqlAdminObjectId string

@description('Entra ID login name (UPN or group name) of the SQL admin.')
param sqlAdminLogin string

@description('Application Gateway autoscale max capacity.')
@minValue(2)
param appGatewayMaxCapacity int = 3

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var vnetAddressPrefix = '10.2.0.0/16'
var linuxFxVersion = 'DOTNETCORE|8.0'

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

module network '../../modules/virtual-network/main.bicep' = {
  name: 'network'
  params: {
    name: '${namePrefix}-vnet'
    location: location
    tags: tags
    addressPrefixes: [
      vnetAddressPrefix
    ]
    subnets: [
      {
        name: 'appGateway'
        prefix: '10.2.0.0/24'
      }
      {
        name: 'appServiceIntegration'
        prefix: '10.2.1.0/24'
        delegations: [
          'Microsoft.Web/serverFarms'
        ]
      }
      {
        name: 'privateEndpoints'
        prefix: '10.2.2.0/24'
      }
    ]
  }
}

var appGatewaySubnetId = first(filter(network.outputs.subnets, s => s.name == 'appGateway')).id
var integrationSubnetId = first(filter(network.outputs.subnets, s => s.name == 'appServiceIntegration')).id
var privateEndpointSubnetId = first(filter(network.outputs.subnets, s => s.name == 'privateEndpoints')).id

module sqlDnsZone '../../modules/private-dns-zone/main.bicep' = {
  name: 'sqlDnsZone'
  params: {
    name: 'privatelink${environment().suffixes.sqlServerHostname}'
    tags: tags
    vnetId: network.outputs.id
  }
}

module kvDnsZone '../../modules/private-dns-zone/main.bicep' = {
  name: 'kvDnsZone'
  params: {
    name: 'privatelink.vaultcore.azure.net'
    tags: tags
    vnetId: network.outputs.id
  }
}

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: '${namePrefix}-sql'
  location: location
  tags: tags
  properties: {
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
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
  name: 'appdb'
  location: location
  tags: tags
  sku: {
    name: sqlSkuName
  }
  properties: {
    zoneRedundant: zoneRedundant
  }
}

module sqlPrivateEndpoint '../../modules/private-endpoint/main.bicep' = {
  name: 'sqlPrivateEndpoint'
  params: {
    name: '${namePrefix}-sql-pe'
    location: location
    tags: tags
    subnetId: privateEndpointSubnetId
    targetResourceId: sqlServer.id
    groupIds: [
      'sqlServer'
    ]
    privateDnsZoneId: sqlDnsZone.outputs.id
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${namePrefix}-kv'
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

module kvPrivateEndpoint '../../modules/private-endpoint/main.bicep' = {
  name: 'kvPrivateEndpoint'
  params: {
    name: '${namePrefix}-kv-pe'
    location: location
    tags: tags
    subnetId: privateEndpointSubnetId
    targetResourceId: keyVault.id
    groupIds: [
      'vault'
    ]
    privateDnsZoneId: kvDnsZone.outputs.id
  }
}

resource appServicePlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
  location: location
  tags: tags
  sku: {
    name: appServiceSku
    capacity: appServiceCapacity
  }
  kind: 'linux'
  properties: {
    reserved: true
    zoneRedundant: zoneRedundant
  }
}

resource webApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-app'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    virtualNetworkSubnetId: integrationSubnetId
    vnetRouteAllEnabled: true
    publicNetworkAccess: 'Disabled'
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      vnetRouteAllEnabled: true
      healthCheckPath: '/health'
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'KeyVaultUri'
          value: keyVault.properties.vaultUri
        }
        {
          name: 'SqlConnectionString'
          value: 'Server=tcp:${sqlServer.properties.fullyQualifiedDomainName},1433;Database=${sqlDatabase.name};Authentication=Active Directory Default;Encrypt=True;'
        }
      ]
    }
  }
}

resource appGatewayPublicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: '${namePrefix}-agw-pip'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  zones: zoneRedundant ? [ '1', '2', '3' ] : null
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource wafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2024-01-01' = {
  name: '${namePrefix}-waf'
  location: location
  tags: tags
  properties: {
    policySettings: {
      state: 'Enabled'
      mode: 'Prevention'
      requestBodyCheck: true
      maxRequestBodySizeInKb: 128
      fileUploadLimitInMb: 100
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'OWASP'
          ruleSetVersion: '3.2'
        }
      ]
    }
  }
}

var appGatewayName = '${namePrefix}-agw'
var appGatewayId = resourceId('Microsoft.Network/applicationGateways', appGatewayName)

resource appGateway 'Microsoft.Network/applicationGateways@2024-01-01' = {
  name: appGatewayName
  location: location
  tags: tags
  zones: zoneRedundant ? [ '1', '2', '3' ] : null
  properties: {
    sku: {
      name: 'WAF_v2'
      tier: 'WAF_v2'
    }
    autoscaleConfiguration: {
      minCapacity: 2
      maxCapacity: appGatewayMaxCapacity
    }
    firewallPolicy: {
      id: wafPolicy.id
    }
    gatewayIPConfigurations: [
      {
        name: 'appGatewayIpConfig'
        properties: {
          subnet: {
            id: appGatewaySubnetId
          }
        }
      }
    ]
    frontendIPConfigurations: [
      {
        name: 'appGatewayFrontendIp'
        properties: {
          publicIPAddress: {
            id: appGatewayPublicIp.id
          }
        }
      }
    ]
    frontendPorts: [
      {
        name: 'port80'
        properties: {
          port: 80
        }
      }
    ]
    backendAddressPools: [
      {
        name: 'webAppBackend'
        properties: {
          backendAddresses: [
            {
              fqdn: webApp.properties.defaultHostName
            }
          ]
        }
      }
    ]
    backendHttpSettingsCollection: [
      {
        name: 'httpsSettings'
        properties: {
          port: 443
          protocol: 'Https'
          cookieBasedAffinity: 'Disabled'
          pickHostNameFromBackendAddress: true
          requestTimeout: 30
          probe: {
            id: '${appGatewayId}/probes/healthProbe'
          }
        }
      }
    ]
    httpListeners: [
      {
        name: 'httpListener'
        properties: {
          frontendIPConfiguration: {
            id: '${appGatewayId}/frontendIPConfigurations/appGatewayFrontendIp'
          }
          frontendPort: {
            id: '${appGatewayId}/frontendPorts/port80'
          }
          protocol: 'Http'
        }
      }
    ]
    requestRoutingRules: [
      {
        name: 'routeToWebApp'
        properties: {
          ruleType: 'Basic'
          priority: 100
          httpListener: {
            id: '${appGatewayId}/httpListeners/httpListener'
          }
          backendAddressPool: {
            id: '${appGatewayId}/backendAddressPools/webAppBackend'
          }
          backendHttpSettings: {
            id: '${appGatewayId}/backendHttpSettingsCollection/httpsSettings'
          }
        }
      }
    ]
    probes: [
      {
        name: 'healthProbe'
        properties: {
          protocol: 'Https'
          path: '/health'
          interval: 30
          timeout: 30
          unhealthyThreshold: 3
          pickHostNameFromBackendHttpSettings: true
          match: {
            statusCodes: [
              '200-399'
            ]
          }
        }
      }
    ]
  }
}

output webAppHostName string = webApp.properties.defaultHostName
output appGatewayPublicIp string = appGatewayPublicIp.properties.ipAddress
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output keyVaultUri string = keyVault.properties.vaultUri
