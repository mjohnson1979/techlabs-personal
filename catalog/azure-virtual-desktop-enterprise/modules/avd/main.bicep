targetScope = 'resourceGroup'

@description('Prefix used for Azure Virtual Desktop resource names.')
param namePrefix string = 'avd'

@description('Metadata region for host pool, app group, and workspace resources.')
param metadataLocation string = 'eastus'

@description('Resource tags.')
param tags object = {}

@description('Maximum user sessions per session host VM.')
@minValue(1)
param maxSessionLimit int = 10

@description('Start stopped session host VMs when users connect.')
param startVmOnConnect bool = true

@description('Enable creation of an AVD scaling plan.')
param enableScalingPlan bool = true

@description('Time zone used by the scaling schedule.')
param scalingTimeZone string = 'Eastern Standard Time'

resource hostPool 'Microsoft.DesktopVirtualization/hostPools@2024-04-03' = {
  name: '${namePrefix}-hp'
  location: metadataLocation
  tags: tags
  properties: {
    hostPoolType: 'Pooled'
    loadBalancerType: 'BreadthFirst'
    preferredAppGroupType: 'Desktop'
    maxSessionLimit: maxSessionLimit
    startVMOnConnect: startVmOnConnect
    validationEnvironment: false
  }
}

resource appGroup 'Microsoft.DesktopVirtualization/applicationGroups@2024-04-03' = {
  name: '${namePrefix}-dag'
  location: metadataLocation
  tags: tags
  properties: {
    hostPoolArmPath: hostPool.id
    applicationGroupType: 'Desktop'
    friendlyName: 'Enterprise Desktop'
  }
}

resource workspace 'Microsoft.DesktopVirtualization/workspaces@2024-04-03' = {
  name: '${namePrefix}-ws'
  location: metadataLocation
  tags: tags
  properties: {
    friendlyName: 'Enterprise Workspace'
    applicationGroupReferences: [
      appGroup.id
    ]
  }
}

resource scalingPlan 'Microsoft.DesktopVirtualization/scalingPlans@2024-04-03' = if (enableScalingPlan) {
  name: '${namePrefix}-sp'
  location: metadataLocation
  tags: tags
  properties: {
    hostPoolType: 'Pooled'
    timeZone: scalingTimeZone
    schedules: [
      {
        name: 'weekday'
        daysOfWeek: [
          'Monday'
          'Tuesday'
          'Wednesday'
          'Thursday'
          'Friday'
        ]
        rampUpStartTime: {
          hour: 7
          minute: 0
        }
        rampUpLoadBalancingAlgorithm: 'BreadthFirst'
        rampUpMinimumHostsPct: 20
        rampUpCapacityThresholdPct: 60
        peakStartTime: {
          hour: 9
          minute: 0
        }
        peakLoadBalancingAlgorithm: 'BreadthFirst'
        rampDownStartTime: {
          hour: 18
          minute: 0
        }
        rampDownLoadBalancingAlgorithm: 'DepthFirst'
        rampDownMinimumHostsPct: 10
        rampDownCapacityThresholdPct: 90
        rampDownForceLogoffUsers: false
        rampDownWaitTimeMinutes: 30
        rampDownNotificationMessage: 'You will be logged off soon. Please save your work.'
        rampDownStopHostsWhen: 'ZeroSessions'
        offPeakStartTime: {
          hour: 20
          minute: 0
        }
        offPeakLoadBalancingAlgorithm: 'DepthFirst'
      }
    ]
    hostPoolReferences: [
      {
        hostPoolArmPath: hostPool.id
        scalingPlanEnabled: true
      }
    ]
  }
}

output hostPoolId string = hostPool.id
output hostPoolName string = hostPool.name
output applicationGroupId string = appGroup.id
output applicationGroupName string = appGroup.name
output workspaceId string = workspace.id
output workspaceName string = workspace.name
