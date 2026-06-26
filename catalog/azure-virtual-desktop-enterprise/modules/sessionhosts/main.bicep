targetScope = 'resourceGroup'

@description('Prefix used for session host resource names.')
param namePrefix string = 'avd'

@description('Azure region for the session host VMs.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Number of session host VMs to deploy.')
@minValue(1)
@maxValue(50)
param sessionHostCount int = 2

@description('VM size for the session hosts.')
param vmSize string = 'Standard_D4s_v5'

@description('Resource ID of the subnet that hosts the session host NICs.')
param subnetId string

@description('Local administrator username for the session hosts.')
param adminUsername string

@description('Local administrator password for the session hosts.')
@secure()
param adminPassword string

@description('Name of the AVD host pool the session hosts register with.')
param hostPoolName string

@description('Host pool registration token.')
@secure()
param hostPoolRegistrationToken string

@description('Marketplace image reference for the session hosts (Windows 11 multi-session by default).')
param imageReference object = {
  publisher: 'MicrosoftWindowsDesktop'
  offer: 'windows-11'
  sku: 'win11-23h2-avd'
  version: 'latest'
}

@description('URL of the AVD agent DSC configuration package used to register session hosts.')
#disable-next-line no-hardcoded-env-urls
param avdAgentDscPackageUrl string = 'https://wvdportalstorageblob.blob.core.windows.net/galleryartifacts/Configuration_1.0.02790.438.zip'

resource nic 'Microsoft.Network/networkInterfaces@2024-01-01' = [for i in range(0, sessionHostCount): {
  name: '${namePrefix}-sh-${i}-nic'
  location: location
  tags: tags
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          subnet: {
            id: subnetId
          }
          privateIPAllocationMethod: 'Dynamic'
        }
      }
    ]
  }
}]

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = [for i in range(0, sessionHostCount): {
  name: '${namePrefix}-sh-${i}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: '${namePrefix}sh${i}'
      adminUsername: adminUsername
      adminPassword: adminPassword
    }
    storageProfile: {
      imageReference: imageReference
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic[i].id
        }
      ]
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
  }
}]

resource aadJoin 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = [for i in range(0, sessionHostCount): {
  parent: vm[i]
  name: 'AADLoginForWindows'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.ActiveDirectory'
    type: 'AADLoginForWindows'
    typeHandlerVersion: '2.0'
    autoUpgradeMinorVersion: true
  }
}]

resource avdAgent 'Microsoft.Compute/virtualMachines/extensions@2024-07-01' = [for i in range(0, sessionHostCount): {
  parent: vm[i]
  name: 'Microsoft.PowerShell.DSC'
  location: location
  properties: {
    publisher: 'Microsoft.Powershell'
    type: 'DSC'
    typeHandlerVersion: '2.73'
    autoUpgradeMinorVersion: true
    settings: {
      modulesUrl: avdAgentDscPackageUrl
      configurationFunction: 'Configuration.ps1\\AddSessionHost'
      properties: {
        hostPoolName: hostPoolName
        aadJoin: true
      }
    }
    protectedSettings: {
      properties: {
        registrationInfoToken: hostPoolRegistrationToken
      }
    }
  }
  dependsOn: [
    aadJoin[i]
  ]
}]

output sessionHostVmIds array = [for i in range(0, sessionHostCount): vm[i].id]
