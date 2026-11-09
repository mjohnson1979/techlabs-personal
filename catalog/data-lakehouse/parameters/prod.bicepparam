using '../main.bicep'

param namePrefix = 'lakeprd'
param lakeStorageSku = 'Standard_ZRS'
param databricksSku = 'premium'
param databricksNoPublicIp = true
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'data-lakehouse'
}
