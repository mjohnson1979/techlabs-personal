using '../main.bicep'

param namePrefix = 'ragprod'
param chatModel = 'gpt-4o-mini'
param chatModelVersion = '2024-07-18'
param chatCapacity = 60
param embeddingModel = 'text-embedding-3-large'
param embeddingModelVersion = '1'
param searchSku = 'standard'
param searchReplicas = 3
param appServiceSku = 'P1v3'
param zoneRedundant = true
param apimSku = 'Premium'
param apimPublisherEmail = 'platform@contoso.com'
param apimPublisherName = 'Contoso Platform'
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'enterprise-rag'
}
