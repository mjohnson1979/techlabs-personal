using '../main.bicep'

param namePrefix = 'acadev'
param minReplicas = 0
param maxReplicas = 5
param zoneRedundant = false
param serviceBusSku = 'Standard'
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'microservices-aca'
}
