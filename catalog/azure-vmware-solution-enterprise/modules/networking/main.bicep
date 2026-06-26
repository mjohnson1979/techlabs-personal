targetScope = 'resourceGroup'

@description('Prefix used for networking resource names.')
param namePrefix string = 'avs'

@description('Azure region for networking resources.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Address space prefixes for the AVS virtual network.')
param vnetAddressPrefixes array = [
  '10.60.0.0/16'
]

@description('Subnet prefix reserved for ExpressRoute gateway.')
param gatewaySubnetPrefix string = '10.60.0.0/27'

@description('Subnet prefix for shared services or jumpbox resources.')
param sharedServicesSubnetPrefix string = '10.60.1.0/24'

module vnet '../../../../modules/virtual-network.bicep' = {
  name: 'vnet'
  params: {
    name: '${namePrefix}-vnet'
    location: location
    tags: tags
    addressPrefixes: vnetAddressPrefixes
    subnets: [
      {
        name: 'GatewaySubnet'
        prefix: gatewaySubnetPrefix
      }
      {
        name: 'shared-services'
        prefix: sharedServicesSubnetPrefix
      }
    ]
  }
}

output vnetId string = vnet.outputs.id
output vnetName string = vnet.outputs.name
output gatewaySubnetId string = [for subnet in vnet.outputs.subnets: if (subnet.name == 'GatewaySubnet') subnet.id][0]
