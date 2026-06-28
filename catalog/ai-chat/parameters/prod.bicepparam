using '../main.bicep'

// Production profile: zone-redundant app tier and higher model throughput.
param environment = 'prod'
param prefix = 'techlabs'
param appServiceSku = 'P1v3'
param appCapacity = 3
param appZoneRedundant = true
param chatModelName = 'gpt-4.1-mini'
param chatModelVersion = '2025-04-14'
param chatModelCapacity = 60
