metadata description = 'Enterprise hub-and-spoke network with Azure Firewall, Bastion, forced tunneling, and a peered workload spoke. The connectivity foundation for a secure landing zone.'

@description('Prefix for resource names.')
param namePrefix string = 'hubspoke'

@description('Azure region.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Address space for the hub VNet.')
param hubAddressPrefix string = '10.0.0.0/16'

@description('Address space for the spoke (workload) VNet.')
param spokeAddressPrefix string = '10.1.0.0/16'

@description('Azure Firewall SKU tier.')
@allowed([
  'Standard'
  'Premium'
])
param firewallTier string = 'Standard'

@description('Deploy Azure Bastion for secure RDP/SSH without public IPs on workloads.')
param deployBastion bool = true

@description('Log Analytics retention in days.')
param retentionInDays int = 30

var hubVnetName = '${namePrefix}-hub-vnet'
var spokeVnetName = '${namePrefix}-spoke-vnet'

module law '../../modules/log-analytics/main.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: location
    tags: tags
    retentionInDays: retentionInDays
  }
}

resource hubVnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: hubVnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        hubAddressPrefix
      ]
    }
    subnets: [
      {
        name: 'AzureFirewallSubnet'
        properties: {
          addressPrefix: cidrSubnet(hubAddressPrefix, 26, 0)
        }
      }
      {
        name: 'AzureBastionSubnet'
        properties: {
          addressPrefix: cidrSubnet(hubAddressPrefix, 26, 1)
        }
      }
      {
        name: 'GatewaySubnet'
        properties: {
          addressPrefix: cidrSubnet(hubAddressPrefix, 27, 4)
        }
      }
    ]
  }
}

resource spokeNsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${namePrefix}-spoke-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'DenyInternetInbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
    ]
  }
}

resource fwPublicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: '${namePrefix}-fw-pip'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  zones: [
    '1'
    '2'
    '3'
  ]
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource firewallPolicy 'Microsoft.Network/firewallPolicies@2024-01-01' = {
  name: '${namePrefix}-fw-policy'
  location: location
  tags: tags
  properties: {
    sku: {
      tier: firewallTier
    }
    threatIntelMode: 'Alert'
  }
}

resource ruleCollectionGroup 'Microsoft.Network/firewallPolicies/ruleCollectionGroups@2024-01-01' = {
  parent: firewallPolicy
  name: 'DefaultNetworkRuleCollectionGroup'
  properties: {
    priority: 200
    ruleCollections: [
      {
        ruleCollectionType: 'FirewallPolicyFilterRuleCollection'
        name: 'spoke-egress'
        priority: 100
        action: {
          type: 'Allow'
        }
        rules: [
          {
            ruleType: 'NetworkRule'
            name: 'allow-https-outbound'
            ipProtocols: [
              'TCP'
            ]
            sourceAddresses: [
              spokeAddressPrefix
            ]
            destinationAddresses: [
              '*'
            ]
            destinationPorts: [
              '443'
            ]
          }
        ]
      }
    ]
  }
}

resource firewall 'Microsoft.Network/azureFirewalls@2024-01-01' = {
  name: '${namePrefix}-fw'
  location: location
  tags: tags
  zones: [
    '1'
    '2'
    '3'
  ]
  properties: {
    sku: {
      name: 'AZFW_VNet'
      tier: firewallTier
    }
    firewallPolicy: {
      id: firewallPolicy.id
    }
    ipConfigurations: [
      {
        name: 'fwIpConfig'
        properties: {
          subnet: {
            id: '${hubVnet.id}/subnets/AzureFirewallSubnet'
          }
          publicIPAddress: {
            id: fwPublicIp.id
          }
        }
      }
    ]
  }
  dependsOn: [
    ruleCollectionGroup
  ]
}

resource bastionPublicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = if (deployBastion) {
  name: '${namePrefix}-bastion-pip'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2024-01-01' = if (deployBastion) {
  name: '${namePrefix}-bastion'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    ipConfigurations: [
      {
        name: 'bastionIpConfig'
        properties: {
          subnet: {
            id: '${hubVnet.id}/subnets/AzureBastionSubnet'
          }
          publicIPAddress: {
            id: bastionPublicIp.id
          }
        }
      }
    ]
  }
}

resource spokeRouteTable 'Microsoft.Network/routeTables@2024-01-01' = {
  name: '${namePrefix}-spoke-rt'
  location: location
  tags: tags
  properties: {
    disableBgpRoutePropagation: true
    routes: [
      {
        name: 'default-via-firewall'
        properties: {
          addressPrefix: '0.0.0.0/0'
          nextHopType: 'VirtualAppliance'
          nextHopIpAddress: firewall.properties.ipConfigurations[0].properties.privateIPAddress
        }
      }
    ]
  }
}

resource spokeVnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: spokeVnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        spokeAddressPrefix
      ]
    }
    subnets: [
      {
        name: 'workload'
        properties: {
          addressPrefix: cidrSubnet(spokeAddressPrefix, 24, 0)
          networkSecurityGroup: {
            id: spokeNsg.id
          }
          routeTable: {
            id: spokeRouteTable.id
          }
        }
      }
      {
        name: 'privateEndpoints'
        properties: {
          addressPrefix: cidrSubnet(spokeAddressPrefix, 24, 1)
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource hubToSpoke 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-01-01' = {
  parent: hubVnet
  name: 'hub-to-spoke'
  properties: {
    remoteVirtualNetwork: {
      id: spokeVnet.id
    }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}

resource spokeToHub 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-01-01' = {
  parent: spokeVnet
  name: 'spoke-to-hub'
  properties: {
    remoteVirtualNetwork: {
      id: hubVnet.id
    }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: true
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}

resource fwDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: firewall
  name: 'fw-diagnostics'
  properties: {
    workspaceId: law.outputs.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output hubVnetId string = hubVnet.id
output spokeVnetId string = spokeVnet.id
output firewallPrivateIp string = firewall.properties.ipConfigurations[0].properties.privateIPAddress
output workloadSubnetId string = '${spokeVnet.id}/subnets/workload'
