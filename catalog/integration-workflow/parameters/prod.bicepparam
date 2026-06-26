using '../main.bicep'

param namePrefix = 'intwfprd'
param workflowSku = 'WS3'
param serviceBusSku = 'Premium'
param zoneRedundant = true
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'integration-workflow'
}
