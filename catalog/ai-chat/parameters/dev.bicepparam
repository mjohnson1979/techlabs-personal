using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param appServiceSku = 'B1'
param appCapacity = 1
param appZoneRedundant = false
param chatModelName = 'gpt-4.1-mini'
param chatModelVersion = '2025-04-14'
param chatModelCapacity = 10
