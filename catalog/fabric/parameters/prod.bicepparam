using '../main.bicep'

param environment = 'prod'
param prefix = 'techlabs'
param skuName = 'F64'
param adminMembers = [
  'fabric-admin@contoso.com'
]
