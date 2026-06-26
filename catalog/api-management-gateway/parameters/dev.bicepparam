using '../main.bicep'

param namePrefix = 'apigwdev'
param apimSku = 'Developer'
param apimCapacity = 1
param apimPublisherEmail = 'platform@contoso.com'
param apimPublisherName = 'Contoso Platform'
param backendSku = 'P0v3'
param zoneRedundant = false
param rateLimitCallsPerMinute = 120
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'api-management-gateway'
}
