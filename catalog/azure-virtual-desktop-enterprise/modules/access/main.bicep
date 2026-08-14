targetScope = 'resourceGroup'

@description('Object ID of the user or group to grant desktop access.')
param principalId string

@description('Type of the principal being granted access.')
@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
param principalType string = 'Group'

@description('Name of the AVD application group to grant access on.')
param applicationGroupName string

// Built-in role: Desktop Virtualization User
var desktopVirtualizationUserRoleId = '1d18fff3-a72a-46b5-b4a9-0b38a3cd7e63'

resource applicationGroup 'Microsoft.DesktopVirtualization/applicationGroups@2024-04-03' existing = {
  name: applicationGroupName
}

resource desktopAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: applicationGroup
  name: guid(applicationGroup.id, principalId, desktopVirtualizationUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', desktopVirtualizationUserRoleId)
    principalId: principalId
    principalType: principalType
  }
}

output roleAssignmentId string = desktopAccess.id
