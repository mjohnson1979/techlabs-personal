using '../main.bicep'

param namePrefix = 'avdprd'
param metadataLocation = 'eastus'
param maxSessionLimit = 8
param startVmOnConnect = true
param enableScalingPlan = true
param scalingTimeZone = 'Eastern Standard Time'
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'avd-hostpool'
}
