using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param containerImage = 'mcr.microsoft.com/k8se/quickstart:latest'
param cpu = '0.5'
param memory = '1.0Gi'
param minReplicas = 0
param maxReplicas = 3
param zoneRedundant = false
param targetPort = 80
