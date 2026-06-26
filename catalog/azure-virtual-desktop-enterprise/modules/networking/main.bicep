targetScope = 'resourceGroup'

@description('Prefix used for networking resource names.')
param namePrefix string = 'avd'

@description('Azure region for networking resources.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Address space prefixes for the AVD virtual network.')
param vnetAddressPrefixes array = [
  '10.40.0.0/16'
]

@description('Subnet prefix used by AVD session hosts.')
param avdSubnetPrefix string = '10.40.1.0/24'

@description('Subnet prefix reserved for private endpoints.')
param privateEndpointSubnetPrefix string = '10.40.2.0/24'

module vnet '../../../../modules/virtual-network.bicep' = {
  name: 'vnet'
  params: {
    name: '${namePrefix}-vnet'
    location: location
    tags: tags
    addressPrefixes: vnetAddressPrefixes
    subnets: [
      {
        name: 'session-hosts'
        prefix: avdSubnetPrefix
      }
      {
        name: 'private-endpoints'
        prefix: privateEndpointSubnetPrefix
        privateEndpointPolicies: true
      }
    ]
  }
}

output vnetId string = vnet.outputs.id
output vnetName string = vnet.outputs.name
output sessionHostsSubnetId string = [for subnet in vnet.outputs.subnets: if (subnet.name == 'session-hosts') subnet.id][0]
output privateEndpointsSubnetId string = [for subnet in vnet.outputs.subnets: if (subnet.name == 'private-endpoints') subnet.id][0]
