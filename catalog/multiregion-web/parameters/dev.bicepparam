using '../main.bicep'

param namePrefix = 'mrwdev'
param primaryLocation = 'eastus'
param secondaryLocation = 'westus3'
param appServiceSku = 'P0v3'
param appServiceCapacity = 1
param zoneRedundant = false
param frontDoorSku = 'Standard_AzureFrontDoor'
param cosmosMultiRegionWrites = false
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'multiregion-web'
}
