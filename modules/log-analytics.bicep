@description('Name of the Log Analytics workspace.')
param name string

@description('Azure region for the workspace.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Data retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
  }
}

output id string = workspace.id
output name string = workspace.name
output customerId string = workspace.properties.customerId
