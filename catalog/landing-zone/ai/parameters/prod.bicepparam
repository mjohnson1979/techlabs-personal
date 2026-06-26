using '../main.bicep'

// Production profile: zone-redundant storage and a scaled, SLA-backed search service.
param environment = 'prod'
param prefix = 'techlabs'
param storageSku = 'Standard_ZRS'
param searchSku = 'standard'
param searchReplicas = 3
param searchPartitions = 1
