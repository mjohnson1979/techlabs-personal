metadata description = 'Active-active multi-region web platform: Azure Front Door Premium with WAF routing to App Service backends in two regions, backed by a multi-region Cosmos DB with automatic failover.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'mrweb'

@description('Primary Azure region.')
param primaryLocation string = 'eastus'

@description('Secondary Azure region.')
param secondaryLocation string = 'westus3'

@description('Resource tags.')
param tags object = {}

@description('App Service plan SKU.')
@allowed([
  'P0v3'
  'P1v3'
  'P2v3'
])
param appServiceSku string = 'P1v3'

@description('Instances per regional App Service plan.')
@minValue(1)
param appServiceCapacity int = 1

@description('Make each regional App Service plan zone redundant.')
param zoneRedundant bool = false

@description('Front Door SKU.')
@allowed([
  'Standard_AzureFrontDoor'
  'Premium_AzureFrontDoor'
])
param frontDoorSku string = 'Premium_AzureFrontDoor'

@description('Enable multi-region writes on Cosmos DB.')
param cosmosMultiRegionWrites bool = false

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var linuxFxVersion = 'DOTNETCORE|8.0'

module law '../../modules/log-analytics.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: primaryLocation
    tags: tags
    retentionInDays: retentionInDays
  }
}

module appInsights '../../modules/app-insights.bicep' = {
  name: 'appInsights'
  params: {
    name: '${namePrefix}-ai'
    location: primaryLocation
    tags: tags
    workspaceResourceId: law.outputs.id
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: toLower('${namePrefix}-cosmos')
  location: primaryLocation
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    enableAutomaticFailover: true
    enableMultipleWriteLocations: cosmosMultiRegionWrites
    minimalTlsVersion: 'Tls12'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: primaryLocation
        failoverPriority: 0
        isZoneRedundant: zoneRedundant
      }
      {
        locationName: secondaryLocation
        failoverPriority: 1
        isZoneRedundant: zoneRedundant
      }
    ]
  }
}

resource primaryPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan-primary'
  location: primaryLocation
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

resource secondaryPlan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan-secondary'
  location: secondaryLocation
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

resource primaryApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-app-primary'
  location: primaryLocation
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: primaryPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/health'
      ipSecurityRestrictionsDefaultAction: 'Deny'
      ipSecurityRestrictions: [
        {
          name: 'AllowFrontDoor'
          priority: 100
          action: 'Allow'
          tag: 'ServiceTag'
          ipAddress: 'AzureFrontDoor.Backend'
          headers: {
            'x-azure-fdid': [
              frontDoorProfile.properties.frontDoorId
            ]
          }
        }
      ]
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'AZURE_COSMOS_ENDPOINT'
          value: cosmos.properties.documentEndpoint
        }
        {
          name: 'REGION'
          value: primaryLocation
        }
      ]
    }
  }
}

resource secondaryApp 'Microsoft.Web/sites@2023-12-01' = {
  name: '${namePrefix}-app-secondary'
  location: secondaryLocation
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: secondaryPlan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: linuxFxVersion
      alwaysOn: true
      http20Enabled: true
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      healthCheckPath: '/health'
      ipSecurityRestrictionsDefaultAction: 'Deny'
      ipSecurityRestrictions: [
        {
          name: 'AllowFrontDoor'
          priority: 100
          action: 'Allow'
          tag: 'ServiceTag'
          ipAddress: 'AzureFrontDoor.Backend'
          headers: {
            'x-azure-fdid': [
              frontDoorProfile.properties.frontDoorId
            ]
          }
        }
      ]
      appSettings: [
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: appInsights.outputs.connectionString
        }
        {
          name: 'AZURE_COSMOS_ENDPOINT'
          value: cosmos.properties.documentEndpoint
        }
        {
          name: 'REGION'
          value: secondaryLocation
        }
      ]
    }
  }
}

resource frontDoorProfile 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: '${namePrefix}-afd'
  location: 'global'
  tags: tags
  sku: {
    name: frontDoorSku
  }
  properties: {
    originResponseTimeoutSeconds: 60
  }
}

resource frontDoorEndpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: frontDoorProfile
  name: '${namePrefix}-endpoint'
  location: 'global'
  properties: {
    enabledState: 'Enabled'
  }
}

resource originGroup 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: frontDoorProfile
  name: 'web-origins'
  properties: {
    loadBalancingSettings: {
      sampleSize: 4
      successfulSamplesRequired: 3
      additionalLatencyInMilliseconds: 50
    }
    healthProbeSettings: {
      probePath: '/health'
      probeRequestType: 'HEAD'
      probeProtocol: 'Https'
      probeIntervalInSeconds: 30
    }
  }
}

resource primaryOrigin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: originGroup
  name: 'primary'
  properties: {
    hostName: primaryApp.properties.defaultHostName
    originHostHeader: primaryApp.properties.defaultHostName
    httpPort: 80
    httpsPort: 443
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
  }
}

resource secondaryOrigin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: originGroup
  name: 'secondary'
  properties: {
    hostName: secondaryApp.properties.defaultHostName
    originHostHeader: secondaryApp.properties.defaultHostName
    httpPort: 80
    httpsPort: 443
    priority: 1
    weight: 1000
    enabledState: 'Enabled'
    enforceCertificateNameCheck: true
  }
}

resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: frontDoorEndpoint
  name: 'default-route'
  properties: {
    originGroup: {
      id: originGroup.id
    }
    supportedProtocols: [
      'Https'
    ]
    patternsToMatch: [
      '/*'
    ]
    forwardingProtocol: 'HttpsOnly'
    linkToDefaultDomain: 'Enabled'
    httpsRedirect: 'Enabled'
  }
  dependsOn: [
    primaryOrigin
    secondaryOrigin
  ]
}

resource wafPolicy 'Microsoft.Network/FrontDoorWebApplicationFirewallPolicies@2024-02-01' = {
  name: '${namePrefix}waf'
  location: 'global'
  tags: tags
  sku: {
    name: frontDoorSku
  }
  properties: {
    policySettings: {
      enabledState: 'Enabled'
      mode: 'Prevention'
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'Microsoft_DefaultRuleSet'
          ruleSetVersion: '2.1'
          ruleSetAction: 'Block'
        }
      ]
    }
  }
}

resource securityPolicy 'Microsoft.Cdn/profiles/securityPolicies@2024-02-01' = {
  parent: frontDoorProfile
  name: 'waf-association'
  properties: {
    parameters: {
      type: 'WebApplicationFirewall'
      wafPolicy: {
        id: wafPolicy.id
      }
      associations: [
        {
          domains: [
            {
              id: frontDoorEndpoint.id
            }
          ]
          patternsToMatch: [
            '/*'
          ]
        }
      ]
    }
  }
}

output frontDoorEndpointHostName string = frontDoorEndpoint.properties.hostName
output primaryAppHostName string = primaryApp.properties.defaultHostName
output secondaryAppHostName string = secondaryApp.properties.defaultHostName
output cosmosEndpoint string = cosmos.properties.documentEndpoint
