targetScope = 'resourceGroup'

@description('Name of the AVS private cloud resource.')
param privateCloudName string = 'avs-private-cloud'

@description('Azure region for AVS.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Address block for AVS management network.')
param networkBlock string = '10.250.0.0/22'

@description('AVS SKU name.')
param skuName string = 'AV36'

@description('Number of AVS nodes in the management cluster.')
@minValue(3)
param clusterSize int = 3

resource privateCloud 'Microsoft.AVS/privateClouds@2023-09-01' = {
  name: privateCloudName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  properties: {
    networkBlock: networkBlock
    managementCluster: {
      clusterSize: clusterSize
    }
    internet: 'Disabled'
  }
}

output privateCloudId string = privateCloud.id
output privateCloudName string = privateCloud.name
