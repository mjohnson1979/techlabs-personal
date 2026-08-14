metadata description = 'Enterprise Azure VMware Solution landing zone: AVS private cloud, landing network, Key Vault and NSG baseline, optional ExpressRoute connectivity, and centralized monitoring.'

targetScope = 'resourceGroup'

@description('Azure region for the landing zone.')
param location string = resourceGroup().location

@description('Environment name, used in resource names and tags.')
param environmentName string = 'dev'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'avs'

@description('AVS private cloud SKU.')
param avsSkuName string = 'AV36'

@description('Number of AVS nodes in the management cluster (minimum 3).')
@minValue(3)
param avsClusterSize int = 3

@description('Address block (/22) for the AVS management network.')
param avsNetworkBlock string = '10.250.0.0/22'

@description('Address space for the landing virtual network.')
param vnetAddressPrefixes array = [
  '10.60.0.0/16'
]

@description('GatewaySubnet prefix for the ExpressRoute gateway.')
param gatewaySubnetPrefix string = '10.60.0.0/27'

@description('Shared services / jumpbox subnet prefix.')
param sharedServicesSubnetPrefix string = '10.60.1.0/24'

@description('Use RBAC for Key Vault data-plane access.')
param enableRbacAuthorization bool = true

@description('Deploy the ExpressRoute gateway for hybrid connectivity.')
param deployConnectivity bool = true

@description('AVS ExpressRoute circuit authorization ID. Leave empty to deploy the gateway without a circuit connection.')
param avsExpressRouteAuthorizationId string = ''

@description('Log Analytics retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

@description('Route AVS control-plane diagnostics to Log Analytics.')
param enableAvsDiagnostics bool = true

var tags = {
  environment: environmentName
  workload: 'azure-vmware-solution-enterprise'
}

module networking 'modules/networking/main.bicep' = {
  name: 'networking'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    vnetAddressPrefixes: vnetAddressPrefixes
    gatewaySubnetPrefix: gatewaySubnetPrefix
    sharedServicesSubnetPrefix: sharedServicesSubnetPrefix
  }
}

module security 'modules/security/main.bicep' = {
  name: 'security'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    enableRbacAuthorization: enableRbacAuthorization
  }
}

module avs 'modules/avs/main.bicep' = {
  name: 'avs'
  params: {
    privateCloudName: '${namePrefix}-private-cloud'
    location: location
    tags: tags
    networkBlock: avsNetworkBlock
    skuName: avsSkuName
    clusterSize: avsClusterSize
  }
}

module connectivity 'modules/connectivity/main.bicep' = if (deployConnectivity) {
  name: 'connectivity'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    virtualNetworkId: networking.outputs.vnetId
    avsExpressRouteAuthorizationId: avsExpressRouteAuthorizationId
  }
}

module monitoring 'modules/monitoring/main.bicep' = {
  name: 'monitoring'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    retentionInDays: retentionInDays
    avsPrivateCloudName: enableAvsDiagnostics ? avs.outputs.privateCloudName : ''
  }
}

@description('Resource ID of the AVS private cloud.')
output privateCloudId string = avs.outputs.privateCloudId

@description('Resource ID of the landing virtual network.')
output vnetId string = networking.outputs.vnetId

@description('Resource ID of the Key Vault baseline.')
output keyVaultId string = security.outputs.keyVaultId

@description('Resource ID of the ExpressRoute gateway, or empty when connectivity is not deployed.')
output expressRouteGatewayId string = connectivity.?outputs.expressRouteGatewayId ?? ''

@description('Resource ID of the Log Analytics workspace.')
output logAnalyticsWorkspaceId string = monitoring.outputs.logAnalyticsWorkspaceId
