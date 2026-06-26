targetScope = 'resourceGroup'

@description('Prefix used for security resource names.')
param namePrefix string = 'avd'

@description('Azure region for security resources.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Enable RBAC authorization on the Key Vault data plane.')
param enableRbacAuthorization bool = true

module keyVault '../../../../modules/key-vault.bicep' = {
  name: 'keyVault'
  params: {
    name: '${namePrefix}-kv'
    location: location
    tags: tags
    enableRbacAuthorization: enableRbacAuthorization
  }
}

resource sessionHostNsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${namePrefix}-sessionhosts-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowRdpFromCorp'
        properties: {
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '3389'
          sourceAddressPrefix: 'VirtualNetwork'
          destinationAddressPrefix: '*'
          access: 'Allow'
          priority: 300
          direction: 'Inbound'
        }
      }
    ]
  }
}

output keyVaultId string = keyVault.outputs.id
output keyVaultName string = keyVault.outputs.name
output sessionHostNsgId string = sessionHostNsg.id
