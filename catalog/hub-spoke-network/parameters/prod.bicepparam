using '../main.bicep'

param namePrefix = 'hsprod'
param hubAddressPrefix = '10.0.0.0/16'
param spokeAddressPrefix = '10.1.0.0/16'
param firewallTier = 'Premium'
param deployBastion = true
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'hub-spoke-network'
}
