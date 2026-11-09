using '../main.bicep'

param namePrefix = 'swaprod'
param appServiceSku = 'P1v3'
param appServiceCapacity = 3
param zoneRedundant = true
param sqlSkuName = 'GP_Gen5_2'
param sqlAdminObjectId = '00000000-0000-0000-0000-000000000000'
param sqlAdminLogin = 'sql-admins'
param appGatewayMaxCapacity = 5
param retentionInDays = 90
param tags = {
  environment: 'prod'
  workload: 'secure-web-app'
}
