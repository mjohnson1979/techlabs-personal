using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param appServiceSku = 'B1'
param appCapacity = 1
param appZoneRedundant = false
// Serverless General Purpose: auto-pauses, cheap for dev.
param sqlSkuName = 'GP_S_Gen5_1'
param sqlTier = 'GeneralPurpose'
param sqlZoneRedundant = false
// TODO: replace with your Entra user/group object id and display name.
param sqlAdminObjectId = '00000000-0000-0000-0000-000000000000'
param sqlAdminLogin = 'sql-admins'
