targetScope = 'resourceGroup'

@description('Prefix used for security resource names.')
param namePrefix string = 'avs'

@description('Azure region for security resources.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Enable RBAC authorization on Key Vault data plane.')
param enableRbacAuthorization bool = true

module keyVault '../../../../modules/key-vault/main.bicep' = {
  name: 'keyVault'
  params: {
    name: '${namePrefix}-kv'
    location: location
    tags: tags
    enableRbacAuthorization: enableRbacAuthorization
  }
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: '${namePrefix}-shared-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'AllowHttpsInboundFromCorp'
        properties: {
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '443'
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
output nsgId string = nsg.id
