using '../main.bicep'

param namePrefix = 'intwfdev'
param workflowSku = 'WS1'
param serviceBusSku = 'Standard'
param zoneRedundant = false
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'integration-workflow'
}
