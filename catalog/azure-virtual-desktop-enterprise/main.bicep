metadata description = 'Enterprise Azure Virtual Desktop landing zone: pooled host pool with app group and workspace, optional scaling plan, FSLogix profile storage, landing network, Key Vault and NSG baseline, and centralized monitoring.'

targetScope = 'resourceGroup'

@description('Azure region for the landing zone.')
param location string = resourceGroup().location

@description('Environment name, used in resource names and tags.')
param environmentName string = 'dev'

@description('Prefix for resource names.')
@minLength(2)
@maxLength(10)
param namePrefix string = 'avd'

@description('Metadata region for the AVD host pool, application group, and workspace control-plane objects.')
param avdMetadataLocation string = 'eastus'

@description('Maximum user sessions per session host VM.')
@minValue(1)
param maxSessionLimit int = 10

@description('Start stopped session host VMs when users connect.')
param startVmOnConnect bool = true

@description('Deploy an AVD scaling plan for the host pool.')
param enableScalingPlan bool = true

@description('Time zone used by the AVD scaling schedule.')
param scalingTimeZone string = 'Eastern Standard Time'

@description('Address space for the landing virtual network.')
param vnetAddressPrefixes array = [
  '10.40.0.0/16'
]

@description('Session-hosts subnet prefix.')
param sessionHostsSubnetPrefix string = '10.40.1.0/24'

@description('Private-endpoints subnet prefix.')
param privateEndpointSubnetPrefix string = '10.40.2.0/24'

@description('Use RBAC for Key Vault data-plane access.')
param enableRbacAuthorization bool = true

@description('Name of the Azure Files share used for FSLogix profile containers.')
param fslogixProfileShareName string = 'profiles'

@description('Log Analytics retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

var tags = {
  environment: environmentName
  workload: 'azure-virtual-desktop-enterprise'
}

var fslogixStorageAccountName = take(toLower(replace('${namePrefix}fsl${uniqueString(resourceGroup().id)}', '-', '')), 24)

module networking 'modules/networking/main.bicep' = {
  name: 'networking'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    vnetAddressPrefixes: vnetAddressPrefixes
    avdSubnetPrefix: sessionHostsSubnetPrefix
    privateEndpointSubnetPrefix: privateEndpointSubnetPrefix
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

module fslogix 'modules/fslogix/main.bicep' = {
  name: 'fslogix'
  params: {
    storageAccountName: fslogixStorageAccountName
    location: location
    tags: tags
    profileShareName: fslogixProfileShareName
  }
}

module avd 'modules/avd/main.bicep' = {
  name: 'avd'
  params: {
    namePrefix: namePrefix
    metadataLocation: avdMetadataLocation
    tags: tags
    maxSessionLimit: maxSessionLimit
    startVmOnConnect: startVmOnConnect
    enableScalingPlan: enableScalingPlan
    scalingTimeZone: scalingTimeZone
  }
}

module monitoring 'modules/monitoring/main.bicep' = {
  name: 'monitoring'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    retentionInDays: retentionInDays
  }
}

@description('Resource ID of the AVD host pool.')
output hostPoolId string = avd.outputs.hostPoolId

@description('Resource ID of the AVD workspace.')
output workspaceId string = avd.outputs.workspaceId

@description('Resource ID of the landing virtual network.')
output vnetId string = networking.outputs.vnetId

@description('Resource ID of the Key Vault baseline.')
output keyVaultId string = security.outputs.keyVaultId

@description('Resource ID of the FSLogix profile storage account.')
output fslogixStorageAccountId string = fslogix.outputs.storageAccountId

@description('Resource ID of the Log Analytics workspace.')
output logAnalyticsWorkspaceId string = monitoring.outputs.logAnalyticsWorkspaceId
