metadata description = 'Deploys SQL Server 2022 Developer on a private Azure VM with private Blob storage and Azure Bastion access.'

targetScope = 'resourceGroup'

@description('Azure region for all regional resources.')
param location string = resourceGroup().location

@description('Environment name used in resource names and tags.')
@allowed([
  'dev'
  'prod'
])
param environment string = 'dev'

@description('Short resource name prefix.')
@minLength(3)
@maxLength(10)
param prefix string = 'techlabs'

@description('SQL Server VM size.')
@allowed([
  'Standard_D2s_v5'
  'Standard_D4s_v5'
  'Standard_D8s_v5'
])
param vmSize string = 'Standard_D4s_v5'

@description('Local administrator username for the SQL Server VM.')
param adminUsername string = 'sqladmin'

@description('Local administrator password for the SQL Server VM.')
@secure()
param adminPassword string = ''

@description('Storage account redundancy SKU.')
@allowed([
  'Standard_LRS'
  'Standard_ZRS'
  'Standard_GRS'
])
param storageSkuName string = 'Standard_LRS'

@description('Address space assigned to the virtual network.')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Address prefix assigned to the SQL VM subnet.')
param vmSubnetPrefix string = '10.20.1.0/24'

@description('Address prefix assigned to the private endpoint subnet.')
param privateEndpointSubnetPrefix string = '10.20.2.0/24'

@description('Address prefix assigned to Azure Bastion. Must be /26 or larger.')
param bastionSubnetPrefix string = '10.20.3.0/26'

var instance = '001'
var resourceSuffix = '${prefix}-${environment}-${location}-${instance}'
var storageName = take('st${toLower(replace(prefix, '-', ''))}${environment}${uniqueString(resourceGroup().id)}', 24)
var blobPrivateDnsZoneName = 'privatelink.blob.${az.environment().suffixes.storage}'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'sql-private-endpoint'
}

module virtualNetwork '../../modules/virtual-network/main.bicep' = {
  name: 'deploy-virtual-network'
  params: {
    name: 'vnet-${resourceSuffix}'
    location: location
    tags: tags
    addressPrefixes: [
      vnetAddressPrefix
    ]
    subnets: [
      {
        name: 'snet-sql-${environment}-${location}-${instance}'
        prefix: vmSubnetPrefix
      }
      {
        name: 'snet-private-endpoints-${environment}-${location}-${instance}'
        prefix: privateEndpointSubnetPrefix
      }
      {
        name: 'AzureBastionSubnet'
        prefix: bastionSubnetPrefix
      }
    ]
  }
}

resource networkInterface 'Microsoft.Network/networkInterfaces@2024-01-01' = {
  name: 'nic-sql-${environment}-${location}-${instance}'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          subnet: {
            id: virtualNetwork.outputs.subnets[0].id
          }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}

resource sqlVirtualMachine 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: 'vm-sql-${environment}-${instance}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: 'sql${environment}${instance}'
      adminUsername: adminUsername
      adminPassword: adminPassword
    }
    storageProfile: {
      imageReference: {
        publisher: 'MicrosoftSQLServer'
        offer: 'sql2022-ws2022'
        sku: 'sqldev-gen2'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: networkInterface.id
        }
      ]
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
  }
}

resource sqlIaasAgent 'Microsoft.SqlVirtualMachine/sqlVirtualMachines@2023-10-01' = {
  name: sqlVirtualMachine.name
  location: location
  tags: tags
  properties: {
    virtualMachineResourceId: sqlVirtualMachine.id
    sqlManagement: 'Full'
    sqlServerLicenseType: 'PAYG'
  }
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: storageSkuName
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Disabled'
  }
}

module blobPrivateDnsZone '../../modules/private-dns-zone/main.bicep' = {
  name: 'deploy-blob-private-dns-zone'
  params: {
    name: blobPrivateDnsZoneName
    tags: tags
    vnetId: virtualNetwork.outputs.id
  }
}

module blobPrivateEndpoint '../../modules/private-endpoint/main.bicep' = {
  name: 'deploy-blob-private-endpoint'
  params: {
    name: 'pep-${prefix}-blob-${environment}-${location}-${instance}'
    location: location
    tags: tags
    subnetId: virtualNetwork.outputs.subnets[1].id
    targetResourceId: storageAccount.id
    groupIds: [
      'blob'
    ]
    privateDnsZoneId: blobPrivateDnsZone.outputs.id
  }
}

resource storageBlobAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, sqlVirtualMachine.id, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    principalId: sqlVirtualMachine.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

resource bastionPublicIp 'Microsoft.Network/publicIPAddresses@2024-01-01' = {
  name: 'pip-bastion-${resourceSuffix}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource bastion 'Microsoft.Network/bastionHosts@2024-01-01' = {
  name: 'bas-${resourceSuffix}'
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
            id: virtualNetwork.outputs.subnets[2].id
          }
          publicIPAddress: {
            id: bastionPublicIp.id
          }
        }
      }
    ]
  }
}

output sqlVirtualMachineName string = sqlVirtualMachine.name
output sqlVirtualMachineResourceId string = sqlVirtualMachine.id
output sqlVirtualMachinePrivateIp string = networkInterface.properties.ipConfigurations[0].properties.privateIPAddress
output storageAccountName string = storageAccount.name
output storageAccountResourceId string = storageAccount.id
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output blobPrivateEndpointResourceId string = blobPrivateEndpoint.outputs.id
output virtualNetworkResourceId string = virtualNetwork.outputs.id
output bastionResourceId string = bastion.id