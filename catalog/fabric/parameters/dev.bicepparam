using '../main.bicep'

param environment = 'dev'
param prefix = 'techlabs'
param skuName = 'F2'
param adminMembers = [
  'fabric-admin@contoso.com'
]
