@description('Deployment location. Purview account availability can vary by region.')
param location string = 'uksouth'

@description('Environment name.')
param environment string = 'dev'

@description('Resource name prefix.')
param prefix string = 'techlabs'

@allowed([
  'Enabled'
  'Disabled'
  'NotSpecified'
])
@description('Public network access setting for the Purview account.')
param publicNetworkAccess string = 'NotSpecified'

@allowed([
  'Enabled'
  'Disabled'
  'NotSpecified'
])
@description('Public network access setting for managed Purview resources.')
param managedResourcesPublicNetworkAccess string = 'NotSpecified'

var accountName = toLower('${prefix}-${environment}-purview-${uniqueString(resourceGroup().id)}')
var tags = {
  Environment: environment
  Owner: 'AzureTechLabs'
  UseCase: 'purview'
}

module account './account/main.bicep' = {
  name: 'purviewAccount'
  params: {
    name: accountName
    location: location
    publicNetworkAccess: publicNetworkAccess
    managedResourcesPublicNetworkAccess: managedResourcesPublicNetworkAccess
    tags: tags
  }
}

output purviewAccountName string = account.outputs.name
output purviewAccountId string = account.outputs.resourceId
output purviewAccountLocation string = account.outputs.location
output purviewManagedResourceGroupName string = account.outputs.managedResourceGroupName
