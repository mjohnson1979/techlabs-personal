metadata description = 'AKS secure baseline: private cluster with Azure CNI overlay, Azure RBAC, workload identity, a Premium container registry with AcrPull, and Container Insights.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'aksbase'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Kubernetes version.')
param kubernetesVersion string = '1.30'

@description('System node pool VM size.')
param systemNodeVmSize string = 'Standard_D4s_v5'

@description('System node pool node count.')
@minValue(1)
param systemNodeCount int = 3

@description('Enable cluster autoscaler on the system pool.')
param enableAutoScaling bool = true

@description('Maximum nodes when autoscaling.')
param maxNodeCount int = 5

@description('Deploy the cluster as a private cluster (no public API server).')
param enablePrivateCluster bool = true

@description('Spread node pool across availability zones.')
param zoneRedundant bool = true

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var vnetAddressPrefix = '10.3.0.0/16'

module law '../../modules/log-analytics.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: location
    tags: tags
    retentionInDays: retentionInDays
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
    subnets: [
      {
        name: 'aks'
        prefix: '10.3.0.0/22'
      }
      {
        name: 'privateEndpoints'
        prefix: '10.3.4.0/24'
      }
    ]
  }
}

var aksSubnetId = first(filter(network.outputs.subnets, s => s.name == 'aks')).id

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: toLower('${namePrefix}acr')
  location: location
  tags: tags
  sku: {
    name: 'Premium'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: zoneRedundant ? 'Enabled' : 'Disabled'
    policies: {
      retentionPolicy: {
        status: 'enabled'
        days: 30
      }
    }
  }
}

resource aks 'Microsoft.ContainerService/managedClusters@2024-09-01' = {
  name: '${namePrefix}-aks'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'Base'
    tier: zoneRedundant ? 'Standard' : 'Free'
  }
  properties: {
    kubernetesVersion: kubernetesVersion
    dnsPrefix: '${namePrefix}-aks'
    enableRBAC: true
    disableLocalAccounts: true
    apiServerAccessProfile: {
      enablePrivateCluster: enablePrivateCluster
    }
    aadProfile: {
      managed: true
      enableAzureRBAC: true
    }
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkPolicy: 'cilium'
      networkDataplane: 'cilium'
      loadBalancerSku: 'standard'
      serviceCidr: '172.16.0.0/16'
      dnsServiceIP: '172.16.0.10'
    }
    addonProfiles: {
      omsagent: {
        enabled: true
        config: {
          logAnalyticsWorkspaceResourceID: law.outputs.id
        }
      }
      azureKeyvaultSecretsProvider: {
        enabled: true
        config: {
          enableSecretRotation: 'true'
        }
      }
    }
    agentPoolProfiles: [
      {
        name: 'system'
        mode: 'System'
        osType: 'Linux'
        osSKU: 'AzureLinux'
        vmSize: systemNodeVmSize
        count: systemNodeCount
        enableAutoScaling: enableAutoScaling
        minCount: enableAutoScaling ? systemNodeCount : null
        maxCount: enableAutoScaling ? maxNodeCount : null
        vnetSubnetID: aksSubnetId
        availabilityZones: zoneRedundant ? [ '1', '2', '3' ] : null
        maxPods: 50
        upgradeSettings: {
          maxSurge: '33%'
        }
      }
    ]
  }
}

@description('Built-in AcrPull role definition ID.')
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: containerRegistry
  name: guid(containerRegistry.id, aks.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: aks.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
  }
}

output aksClusterName string = aks.name
output aksOidcIssuerUrl string = aks.properties.oidcIssuerProfile.issuerURL
output containerRegistryLoginServer string = containerRegistry.properties.loginServer
output kubeletIdentityObjectId string = aks.properties.identityProfile.kubeletidentity.objectId
