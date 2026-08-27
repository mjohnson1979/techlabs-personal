<#
.SYNOPSIS
Triggers or resets an Azure SRE Agent demo scenario.

.DESCRIPTION
Changes only the specified Azure Container App. AutoRemediation disables ingress,
CostOptimization restores three idle replicas, SecurityPosture allows insecure
HTTP, and Reset restores the secure healthy baseline.

.PARAMETER Scenario
The scenario state to apply.

.PARAMETER SubscriptionId
The Azure subscription containing the demo resource group.

.PARAMETER ResourceGroupName
The resource group containing the isolated demo workload.

.PARAMETER ContainerAppName
The exact name of the demo Container App.

.PARAMETER TargetPort
The port exposed by the demo container image.

.EXAMPLE
.\Set-SreDemoScenario.ps1 -Scenario AutoRemediation -SubscriptionId $subscriptionId -ResourceGroupName rg-sre-demo -ContainerAppName ca-sredemo-dev-eastus2-001

.EXAMPLE
.\Set-SreDemoScenario.ps1 -Scenario Reset -SubscriptionId $subscriptionId -ResourceGroupName rg-sre-demo -ContainerAppName ca-sredemo-dev-eastus2-001 -WhatIf

.OUTPUTS
PSCustomObject describing the resulting Container App configuration.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory)]
    [ValidateSet('AutoRemediation', 'CostOptimization', 'SecurityPosture', 'Reset')]
    [string]$Scenario,

    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-fA-F-]{36}$')]
    [string]$SubscriptionId,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$ResourceGroupName,

    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z][a-z0-9-]{0,30}[a-z0-9]$')]
    [string]$ContainerAppName,

    [Parameter()]
    [ValidateRange(1, 65535)]
    [int]$TargetPort = 80
)

$ErrorActionPreference = 'Stop'

function Invoke-AzureCli {
    param(
        [Parameter(Mandatory)]
        [string[]]$ArgumentList
    )

    $cliArguments = @($ArgumentList) + '--only-show-errors'
    $output = & az @cliArguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI failed: $($output -join [Environment]::NewLine)"
    }

    return $output
}

Invoke-AzureCli -ArgumentList @('account', 'set', '--subscription', $SubscriptionId) | Out-Null

$resourceId = Invoke-AzureCli -ArgumentList @(
    'containerapp', 'show',
    '--resource-group', $ResourceGroupName,
    '--name', $ContainerAppName,
    '--query', 'id',
    '--output', 'tsv'
)

$expectedResourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.App/containerApps/$ContainerAppName"
if (-not [string]::Equals($resourceId.Trim(), $expectedResourceId, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved resource ID does not match the requested demo Container App."
}

$arguments = switch ($Scenario) {
    'AutoRemediation' {
        @('containerapp', 'ingress', 'disable', '--resource-group', $ResourceGroupName, '--name', $ContainerAppName)
    }
    'CostOptimization' {
        @('containerapp', 'update', '--resource-group', $ResourceGroupName, '--name', $ContainerAppName, '--min-replicas', '3')
    }
    'SecurityPosture' {
        @('containerapp', 'ingress', 'update', '--resource-group', $ResourceGroupName, '--name', $ContainerAppName, '--allow-insecure', 'true')
    }
    'Reset' {
        @(
            'containerapp', 'ingress', 'enable',
            '--resource-group', $ResourceGroupName,
            '--name', $ContainerAppName,
            '--type', 'external',
            '--target-port', $TargetPort.ToString(),
            '--transport', 'auto',
            '--allow-insecure', 'false'
        )
    }
}

if ($PSCmdlet.ShouldProcess($expectedResourceId, "Apply $Scenario scenario state")) {
    Invoke-AzureCli -ArgumentList $arguments | Out-Null

    if ($Scenario -eq 'Reset') {
        Invoke-AzureCli -ArgumentList @(
            'containerapp', 'update',
            '--resource-group', $ResourceGroupName,
            '--name', $ContainerAppName,
            '--min-replicas', '3',
            '--max-replicas', '5'
        ) | Out-Null
    }
}

$stateJson = Invoke-AzureCli -ArgumentList @(
    'containerapp', 'show',
    '--resource-group', $ResourceGroupName,
    '--name', $ContainerAppName,
    '--query', '{resourceId:id,fqdn:properties.configuration.ingress.fqdn,external:properties.configuration.ingress.external,allowInsecure:properties.configuration.ingress.allowInsecure,minReplicas:properties.template.scale.minReplicas,maxReplicas:properties.template.scale.maxReplicas}',
    '--output', 'json'
)

$state = $stateJson | ConvertFrom-Json
[PSCustomObject]@{
    Scenario      = $Scenario
    ResourceId    = $state.resourceId
    Fqdn          = $state.fqdn
    External      = $state.external
    AllowInsecure = $state.allowInsecure
    MinReplicas   = $state.minReplicas
    MaxReplicas   = $state.maxReplicas
}