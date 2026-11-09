@description('Deployment location.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@allowed([
  'F2'
  'F4'
  'F8'
  'F16'
  'F32'
  'F64'
  'F128'
  'F256'
  'F512'
  'F1024'
  'F2048'
])
@description('Fabric capacity SKU size.')
param skuName string = 'F2'

@description('Fabric capacity admins (UPN/email list).')
param adminMembers array = [
  'fabric-admin@contoso.com'
]

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'fabric'
}

resource fabricCapacity 'Microsoft.Fabric/capacities@2023-11-01' = {
  name: '${prefix}-${environment}-fabric-${uniqueString(resourceGroup().id)}'
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: 'Fabric'
  }
  properties: {
    administration: {
      members: adminMembers
    }
  }
}

output fabricCapacityName string = fabricCapacity.name
output fabricCapacityId string = fabricCapacity.id
output fabricCapacityLocation string = fabricCapacity.location
