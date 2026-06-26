using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param appServiceSku = 'B1'
param appCapacity = 1
param appZoneRedundant = false
param chatModelName = 'gpt-4o-mini'
param chatModelVersion = '2024-07-18'
param chatModelCapacity = 10
