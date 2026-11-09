using '../main.bicep'

param namePrefix = 'mrwprd'
param primaryLocation = 'eastus'
param secondaryLocation = 'westus3'
param appServiceSku = 'P1v3'
param appServiceCapacity = 3
param zoneRedundant = true
param frontDoorSku = 'Premium_AzureFrontDoor'
param cosmosMultiRegionWrites = true
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'multiregion-web'
}
