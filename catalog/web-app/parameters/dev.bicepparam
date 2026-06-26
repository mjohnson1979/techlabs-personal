using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param sku = 'B1'
param capacity = 1
param zoneRedundant = false
param linuxFxVersion = 'DOTNETCORE|8.0'
