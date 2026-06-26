using '../main.bicep'

param namePrefix = 'apigwprd'
param apimSku = 'Premium'
param apimCapacity = 2
param apimPublisherEmail = 'platform@contoso.com'
param apimPublisherName = 'Contoso Platform'
param backendSku = 'P1v3'
param zoneRedundant = true
param rateLimitCallsPerMinute = 600
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'api-management-gateway'
}
