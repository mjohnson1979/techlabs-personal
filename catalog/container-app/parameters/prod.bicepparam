using '../main.bicep'

// Production profile: zone-redundant environment, warm baseline (no cold starts).
param environment = 'prod'
param prefix = 'techlabs'
param containerImage = 'mcr.microsoft.com/k8se/quickstart:latest'
param cpu = '1.0'
param memory = '2.0Gi'
param minReplicas = 2
param maxReplicas = 10
param zoneRedundant = true
param targetPort = 80
