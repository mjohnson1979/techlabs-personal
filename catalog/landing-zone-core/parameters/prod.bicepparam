using '../main.bicep'

// Production profile: zone-redundant storage for resiliency.
param environment = 'prod'
param prefix = 'techlabs'
param storageSku = 'Standard_ZRS'
