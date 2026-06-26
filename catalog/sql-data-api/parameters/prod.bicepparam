using '../main.bicep'

// Production profile: zone-redundant app tier and zone-redundant SQL database.
param environment = 'prod'
param prefix = 'techlabs'
param appServiceSku = 'P1v3'
param appCapacity = 3
param appZoneRedundant = true
// Provisioned General Purpose with zone redundancy.
param sqlSkuName = 'GP_Gen5_2'
param sqlTier = 'GeneralPurpose'
param sqlZoneRedundant = true
// TODO: replace with your Entra user/group object id and display name.
param sqlAdminObjectId = '00000000-0000-0000-0000-000000000000'
param sqlAdminLogin = 'sql-admins'
