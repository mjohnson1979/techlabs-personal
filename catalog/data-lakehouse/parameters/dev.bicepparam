using '../main.bicep'

param namePrefix = 'lakedev'
param lakeStorageSku = 'Standard_LRS'
param databricksSku = 'standard'
param databricksNoPublicIp = false
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'data-lakehouse'
}
