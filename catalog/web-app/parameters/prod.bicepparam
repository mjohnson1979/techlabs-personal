using '../main.bicep'

// Production profile: zone-redundant Premium v3 plan across 3 availability zones.
param environment = 'prod'
param prefix = 'techlabs'
param sku = 'P1v3'
param capacity = 3
param zoneRedundant = true
param linuxFxVersion = 'DOTNETCORE|8.0'
