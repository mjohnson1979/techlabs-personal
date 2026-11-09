@description('Deployment location.')
param location string = resourceGroup().location

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@description('Power BI capacity admin member object IDs.')
param members array = [
  '00000000-0000-0000-0000-000000000000'
]

@allowed([
  'A1'
  'A2'
  'A3'
  'A4'
  'A5'
  'A6'
])
@description('Power BI Dedicated capacity SKU name.')
param skuName string = 'A1'

@description('Power BI Dedicated capacity units.')
param skuCapacity int = 1

var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'power-bi-dedicated'
}

module capacity './capacity/main.bicep' = {
  name: 'powerBiDedicatedCapacity'
  params: {
    name: toLower('${prefix}-${environment}-pbi-${uniqueString(resourceGroup().id)}')
    location: location
    members: members
    sku: {
      name: skuName
      capacity: skuCapacity
      tier: 'PBIE_Azure'
    }
    tags: tags
  }
}

output powerBiCapacityName string = capacity.outputs.name
output powerBiCapacityId string = capacity.outputs.resourceId
output powerBiCapacityLocation string = capacity.outputs.location
