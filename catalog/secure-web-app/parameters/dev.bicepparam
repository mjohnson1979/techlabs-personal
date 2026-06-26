using '../main.bicep'

param namePrefix = 'swadev'
param appServiceSku = 'P0v3'
param appServiceCapacity = 1
param zoneRedundant = false
param sqlSkuName = 'GP_S_Gen5_1'
param sqlAdminObjectId = '00000000-0000-0000-0000-000000000000'
param sqlAdminLogin = 'sql-admins'
param appGatewayMaxCapacity = 2
param retentionInDays = 30
param tags = {
  environment: 'dev'
  workload: 'secure-web-app'
}
