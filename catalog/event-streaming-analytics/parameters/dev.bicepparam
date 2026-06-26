using '../main.bicep'

param namePrefix = 'strmdev'
param eventHubsSku = 'Standard'
param throughputUnits = 1
param partitionCount = 2
param messageRetentionDays = 1
param streamingUnits = 1
param functionPlanSku = 'Y1'
param zoneRedundant = false
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'event-streaming-analytics'
}
