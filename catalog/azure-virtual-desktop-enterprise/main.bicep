targetScope = 'resourceGroup'

@description('Deployment location')
param location string = resourceGroup().location

@description('Environment name')
param environmentName string = 'dev'

// TODO: Compose enterprise AVD modules from ./modules as implementation is added.
output deploymentName string = 'azure-virtual-desktop-enterprise-${environmentName}'
