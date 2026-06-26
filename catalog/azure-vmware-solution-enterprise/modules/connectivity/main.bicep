targetScope = 'resourceGroup'

@description('Prefix used for connectivity resource names.')
param namePrefix string = 'avs'

@description('Azure region for connectivity resources.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Resource ID of the virtual network that hosts GatewaySubnet.')
param virtualNetworkId string

@description('Resource ID of the AVS ExpressRoute circuit authorization. Leave empty to skip connection.')
param avsExpressRouteAuthorizationId string = ''

resource gatewayPublicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: '${namePrefix}-ergw-pip'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource erGateway 'Microsoft.Network/virtualNetworkGateways@2024-01-01' = {
  name: '${namePrefix}-ergw'
  location: location
  tags: tags
  properties: {
    gatewayType: 'ExpressRoute'
    vpnType: 'RouteBased'
    enableBgp: false
    sku: {
      name: 'ErGw2AZ'
      tier: 'ErGw2AZ'
    }
    ipConfigurations: [
      {
        name: 'gateway-ipconfig'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: {
            id: gatewayPublicIp.id
          }
          subnet: {
            id: '${virtualNetworkId}/subnets/GatewaySubnet'
          }
        }
      }
    ]
  }
}

resource erConnection 'Microsoft.Network/connections@2024-01-01' = if (!empty(avsExpressRouteAuthorizationId)) {
  name: '${namePrefix}-avs-er-conn'
  location: location
  tags: tags
  properties: {
    connectionType: 'ExpressRoute'
    virtualNetworkGateway1: {
      id: erGateway.id
    }
    peer: {
      id: avsExpressRouteAuthorizationId
    }
  }
}

output expressRouteGatewayId string = erGateway.id
output expressRouteGatewayName string = erGateway.name
output expressRouteConnectionId string = !empty(avsExpressRouteAuthorizationId) ? erConnection.id : ''
