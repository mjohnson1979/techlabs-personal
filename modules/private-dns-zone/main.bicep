@description('Private DNS zone name, e.g. privatelink.blob.core.windows.net.')
param name string

@description('Resource tags.')
param tags object = {}

@description('Resource ID of the virtual network to link to this zone.')
param vnetId string

@description('Whether to enable auto-registration on the VNet link.')
param registrationEnabled bool = false

resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: name
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: zone
  name: '${name}-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: registrationEnabled
    virtualNetwork: {
      id: vnetId
    }
  }
}

output id string = zone.id
output name string = zone.name
