using '../main.bicep'

param namePrefix = 'hsdev'
param hubAddressPrefix = '10.0.0.0/16'
param spokeAddressPrefix = '10.1.0.0/16'
param firewallTier = 'Standard'
param deployBastion = false
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'hub-spoke-network'
}
