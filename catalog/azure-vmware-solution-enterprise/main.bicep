targetScope = 'resourceGroup'

@description('Deployment location')
param location string = resourceGroup().location

@description('Environment name')
param environmentName string = 'dev'

// TODO: Compose enterprise AVS modules from ./modules as implementation is added.
output deploymentName string = 'azure-vmware-solution-enterprise-${environmentName}'
