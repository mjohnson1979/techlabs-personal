@description('Name of the virtual network.')
param name string

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Address space prefixes for the VNet.')
param addressPrefixes array = [
  '10.0.0.0/16'
]

@description('Subnets to create. Each item: { name, prefix, delegations (optional array of service names), privateEndpointPolicies (optional bool) }.')
param subnets array = []

@description('Optional resource ID of an NSG to associate with every subnet that does not specify its own.')
param defaultNsgId string = ''

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: addressPrefixes
    }
    subnets: [
      for subnet in subnets: {
        name: subnet.name
        properties: {
          addressPrefix: subnet.prefix
          networkSecurityGroup: !empty(defaultNsgId) && (subnet.?associateNsg ?? true) ? {
            id: defaultNsgId
          } : null
          delegations: map(subnet.?delegations ?? [], del => {
            name: 'delegation-${replace(del, '/', '-')}'
            properties: {
              serviceName: del
            }
          })
          privateEndpointNetworkPolicies: (subnet.?privateEndpointPolicies ?? false) ? 'Enabled' : 'Disabled'
        }
      }
    ]
  }
}

output id string = vnet.id
output name string = vnet.name
output subnets array = [
  for (subnet, i) in subnets: {
    name: subnet.name
    id: vnet.properties.subnets[i].id
  }
]
