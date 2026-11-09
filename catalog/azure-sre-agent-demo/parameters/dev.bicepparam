using '../main.bicep'

param location = 'eastus2'
param environment = 'dev'
param workloadName = 'sredemo'
param containerImage = 'mcr.microsoft.com/k8se/quickstart:latest'
param targetPort = 80
param minReplicas = 3
param maxReplicas = 5
param sponsorGroupObjectId = '5734b843-4ae2-4e07-8334-e3572f0b833d'