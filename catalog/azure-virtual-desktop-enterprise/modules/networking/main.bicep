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

@description('Optional NSG resource ID to associate with the session-hosts subnet. Leave empty to skip.')
param sessionHostsNsgId string = ''

module vnet '../../../../modules/virtual-network/main.bicep' = {
  name: 'vnet'
  params: {
    name: '${namePrefix}-vnet'
    location: location
    tags: tags
    addressPrefixes: vnetAddressPrefixes
    defaultNsgId: sessionHostsNsgId
    subnets: [
      {
        name: 'session-hosts'
        prefix: avdSubnetPrefix
      }
      {
        name: 'private-endpoints'
        prefix: privateEndpointSubnetPrefix
        privateEndpointPolicies: true
        associateNsg: false
      }
    ]
  }
}

output vnetId string = vnet.outputs.id
output vnetName string = vnet.outputs.name
output sessionHostsSubnetId string = first(filter(vnet.outputs.subnets, subnet => subnet.name == 'session-hosts')).id
output privateEndpointsSubnetId string = first(filter(vnet.outputs.subnets, subnet => subnet.name == 'private-endpoints')).id
