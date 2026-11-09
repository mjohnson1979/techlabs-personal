using '../main.bicep'

param namePrefix = 'aksprod'
param kubernetesVersion = '1.30'
param systemNodeVmSize = 'Standard_D4s_v5'
param systemNodeCount = 3
param enableAutoScaling = true
param maxNodeCount = 6
param enablePrivateCluster = true
param zoneRedundant = true
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'aks-secure-baseline'
}
