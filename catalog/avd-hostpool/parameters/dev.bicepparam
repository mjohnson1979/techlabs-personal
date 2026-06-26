using '../main.bicep'

param namePrefix = 'avddev'
param metadataLocation = 'eastus'
param maxSessionLimit = 16
param startVmOnConnect = true
param enableScalingPlan = false
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'avd-hostpool'
}
