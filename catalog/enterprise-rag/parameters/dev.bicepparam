using '../main.bicep'

param namePrefix = 'ragdev'
param chatModel = 'gpt-5-mini'
param chatModelVersion = '2025-08-07'
param chatModelSku = 'GlobalStandard'
param chatCapacity = 10
param embeddingModel = 'text-embedding-3-large'
param embeddingModelVersion = '1'
param searchSku = 'basic'
param searchReplicas = 1
param appServiceSku = 'P0v3'
param zoneRedundant = false
param apimSku = 'Developer'
param apimPublisherEmail = 'platform@contoso.com'
param apimPublisherName = 'Contoso Platform'
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'enterprise-rag'
}
