using '../main.bicep'

param namePrefix = 'strmprd'
param eventHubsSku = 'Premium'
param throughputUnits = 2
param partitionCount = 8
param messageRetentionDays = 7
param streamingUnits = 6
param functionPlanSku = 'EP1'
param zoneRedundant = true
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'event-streaming-analytics'
}
