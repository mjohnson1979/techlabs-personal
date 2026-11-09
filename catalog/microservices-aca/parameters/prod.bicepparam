using '../main.bicep'

param namePrefix = 'acaprd'
param minReplicas = 2
param maxReplicas = 20
param zoneRedundant = true
param serviceBusSku = 'Premium'
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'microservices-aca'
}
