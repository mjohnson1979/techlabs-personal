using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param appServiceSku = 'B1'
param appCapacity = 1
param appZoneRedundant = false
param chatModelName = 'gpt-5-mini'
param chatModelVersion = '2025-08-07'
param chatModelSku = 'GlobalStandard'
param chatModelCapacity = 10
