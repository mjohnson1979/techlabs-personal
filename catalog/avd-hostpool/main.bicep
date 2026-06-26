metadata description = 'Azure Virtual Desktop landing zone: a pooled host pool, desktop application group, workspace, and an autoscaling plan with diagnostics to Log Analytics - the control plane for secure remote desktops.'

@description('Prefix for resource names.')
@minLength(2)
param namePrefix string = 'avd'

@description('Azure region for session hosts and VMs.')
param location string = resourceGroup().location

@description('Metadata region for AVD objects (host pool, app group, workspace).')
param metadataLocation string = 'eastus'

@description('Resource tags.')
param tags object = {}

@description('Maximum sessions per session host.')
@minValue(1)
param maxSessionLimit int = 10

@description('Start session host VMs on connect to save cost.')
param startVmOnConnect bool = true

@description('Enable the autoscaling plan.')
param enableScalingPlan bool = true

@description('Time zone for the scaling plan schedule.')
param scalingTimeZone string = 'Eastern Standard Time'

@description('Log Analytics retention in days.')
param retentionInDays int = 30

module law '../../modules/log-analytics.bicep' = {
  name: 'law'
  params: {
    name: '${namePrefix}-law'
    location: location
    tags: tags
    retentionInDays: retentionInDays
  }
}

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
    customRdpProperty: 'audiocapturemode:i:1;audiomode:i:0;drivestoredirect:s:;redirectclipboard:i:0;redirectprinters:i:0;'
  }
}

resource appGroup 'Microsoft.DesktopVirtualization/applicationGroups@2024-04-03' = {
  name: '${namePrefix}-dag'
  location: metadataLocation
  tags: tags
  properties: {
    hostPoolArmPath: hostPool.id
    applicationGroupType: 'Desktop'
    friendlyName: 'Corporate Desktop'
  }
}

resource workspace 'Microsoft.DesktopVirtualization/workspaces@2024-04-03' = {
  name: '${namePrefix}-ws'
  location: metadataLocation
  tags: tags
  properties: {
    friendlyName: 'Corporate Workspace'
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

resource hostPoolDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: hostPool
  name: 'hp-diagnostics'
  properties: {
    workspaceId: law.outputs.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

output hostPoolName string = hostPool.name
output applicationGroupName string = appGroup.name
output workspaceName string = workspace.name
