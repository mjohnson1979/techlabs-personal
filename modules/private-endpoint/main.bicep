@description('Name of the private endpoint.')
param name string

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Resource ID of the subnet the private endpoint NIC is placed in.')
param subnetId string

@description('Resource ID of the Azure resource to connect to privately (e.g. a storage account, Key Vault, SQL server).')
param targetResourceId string

@description('Group IDs (sub-resources) to connect, e.g. [ \'blob\' ] for storage, [ \'vault\' ] for Key Vault, [ \'sqlServer\' ] for Azure SQL.')
param groupIds array

@description('Optional private DNS zone resource ID to auto-register the endpoint record. Leave empty to skip DNS integration.')
param privateDnsZoneId string = ''

resource privateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    subnet: {
      id: subnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${name}-conn'
        properties: {
          privateLinkServiceId: targetResourceId
          groupIds: groupIds
        }
      }
    ]
  }
}

resource dnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (!empty(privateDnsZoneId)) {
  parent: privateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'default'
        properties: {
          privateDnsZoneId: privateDnsZoneId
        }
      }
    ]
  }
}

output id string = privateEndpoint.id
output name string = privateEndpoint.name
