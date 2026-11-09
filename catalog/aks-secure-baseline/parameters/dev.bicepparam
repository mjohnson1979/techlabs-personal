using '../main.bicep'

param namePrefix = 'aksdev'
param kubernetesVersion = '1.30'
param systemNodeVmSize = 'Standard_D2s_v5'
param systemNodeCount = 1
param enableAutoScaling = false
param maxNodeCount = 2
param enablePrivateCluster = false
param zoneRedundant = false
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'aks-secure-baseline'
}
