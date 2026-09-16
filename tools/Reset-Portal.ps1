#Requires -Version 7.0

<#
.SYNOPSIS
Starts a fresh portal installation on existing App Service infrastructure.
.DESCRIPTION
Run locally with Azure CLI. Archives by detaching the existing data directory,
not deleting it. Stops the app, switches its database and Terraform work paths,
clears legacy GitHub/subscription inputs, verifies settings, then starts the app.
Preserves Entra registration/sign-in, managed identity, image, networking and
all Azure resources. No SQL, filesystem deletion, Graph or GitHub calls are made.
Old workloads remain billable and will no longer be automatically cleaned up.
Back up existing data and protected settings before applying; this is not a
secure erase or a backup. Recovery requires restoring the old paths and any
cleared connection settings from your protected backup while the app is stopped.
.PARAMETER SubscriptionId
Hosting subscription ID. Prompted when omitted.
.PARAMETER ResourceGroupName
Existing portal resource group. Prompted when omitted.
.PARAMETER AppName
Existing portal App Service name. Prompted when omitted.
.PARAMETER AcknowledgeUnmanagedDeployments
Required for apply. Confirms existing workloads have been resolved or their
ongoing costs and manual cleanup responsibility are explicitly accepted.
.PARAMETER NonInteractive
Require all inputs and explicit -Confirm:$false. Never bypasses acknowledgement.
.EXAMPLE
./tools/Reset-Portal.ps1 -WhatIf
.EXAMPLE
./tools/Reset-Portal.ps1 -AcknowledgeUnmanagedDeployments
.NOTES
Does not run on import. Requires the neighboring Deploy-Portal.ps1 helpers.
Only supports the standard organization container and persistent /home/data paths.
On failure after stop, leaves the app stopped where possible for inspection.
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [string]$SubscriptionId,
    [string]$ResourceGroupName,
    [string]$AppName,
    [switch]$AcknowledgeUnmanagedDeployments,
    [switch]$NonInteractive
)

$resetParameters = @{} + $PSBoundParameters
. "$PSScriptRoot/Deploy-Portal.ps1"

function Get-PortalResetSnapshot {
    param([string]$Subscription, [string]$AppResourceId)
    $site = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId`?api-version=2024-11-01"
    $planId = [string]$site.properties.serverFarmId
    if ($planId -notlike "/subscriptions/$Subscription/*" -or $planId -notmatch '/providers/Microsoft.Web/serverfarms/[^/]+$') {
        throw 'The hosting plan could not be verified.'
    }
    return @{
        Subscription = Invoke-PortalAzJson -Arguments @('account', 'show', '--subscription', $Subscription) -Operation 'reset subscription verification'
        Site = $site
        Plan = Invoke-PortalArm -Subscription $Subscription -Path "$planId`?api-version=2024-11-01"
        Web = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId/config/web?api-version=2024-11-01"
        Authentication = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId/config/authsettingsV2?api-version=2024-11-01"
        Settings = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId/config/appsettings/list?api-version=2024-11-01" -Method POST
    }
}

function Get-PortalResetPlan {
    param([hashtable]$Snapshot, [string]$AppResourceId)
    $subscription = ConvertTo-PortalGuid $Snapshot.Subscription.id
    $tenant = ConvertTo-PortalGuid $Snapshot.Subscription.tenantId
    $site = $Snapshot.Site
    $values = $Snapshot.Settings.properties
    $auth = $Snapshot.Authentication.properties
    $provider = $auth.identityProviders.azureActiveDirectory
    if ($Snapshot.Subscription.environmentName -ne 'AzureCloud' -or $Snapshot.Subscription.state -ne 'Enabled' -or
        $site.id -ine $AppResourceId -or $AppResourceId -notlike "/subscriptions/$subscription/*" -or
        $site.kind -notmatch 'linux' -or $site.properties.httpsOnly -ne $true -or $site.properties.state -ne 'Running') {
        throw 'Select the running Linux HTTPS portal in the intended subscription.'
    }
    if ($Snapshot.Web.properties.linuxFxVersion -notmatch '^DOCKER\|[a-z0-9]+\.azurecr\.io/deployment-portal(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[a-f0-9]{64})$' -or
        $values.WEBSITES_ENABLE_APP_SERVICE_STORAGE -ne 'true' -or $values.GALLERY_EASY_AUTH -ne 'true') {
        throw 'Reset requires the standard organization portal container with persistent storage and Easy Auth.'
    }
    if ($Snapshot.Plan.properties.numberOfWorkers -ne 1 -or $Snapshot.Plan.properties.elasticScaleEnabled -eq $true) {
        throw 'Reset requires a single-instance portal without automatic scaling.'
    }
    $client = ConvertTo-PortalGuid $provider.registration.clientId
    if ($auth.platform.enabled -ne $true -or $auth.httpSettings.requireHttps -ne $true -or
        $auth.globalValidation.requireAuthentication -ne $true -or @($auth.globalValidation.excludedPaths | Where-Object { $_ }).Count -gt 0 -or
        $provider.enabled -ne $true -or $provider.registration.openIdIssuer -cne "https://login.microsoftonline.com/$tenant/v2.0" -or
        @($provider.validation.allowedAudiences | Where-Object { $_ -and $_ -ne $client }).Count -gt 0) {
        throw 'Authenticated, tenant-scoped Entra sign-in must remain configured before resetting.'
    }
    $admins = @(([string]$values.GALLERY_ADMIN_OIDS).Split(',', [StringSplitOptions]::RemoveEmptyEntries))
    if (-not $admins.Count) { throw 'Retain an explicit initial administrator before resetting.' }
    foreach ($admin in $admins) { $null = ConvertTo-PortalGuid $admin.Trim() }
    if ($values.GALLERY_INSTALLER_ENABLED -in @('true','1','yes')) { throw 'Complete or retire the active code installer first.' }
    $database = if ($values.GALLERY_DB_PATH) { [string]$values.GALLERY_DB_PATH } else { '/home/data/deployment-portal.db' }
    $terraform = if ($values.GALLERY_TF_WORK) { [string]$values.GALLERY_TF_WORK } else { '/home/data/tf-runs' }
    if ($database -notmatch '^/home/data/(?:portal-reset/[a-f0-9]{32}/)?deployment-portal\.db$' -or
        $terraform -notmatch '^/home/data/(?:portal-reset/[a-f0-9]{32}/)?tf-runs$') {
        throw 'Custom data paths require a separately reviewed reset; no paths were changed.'
    }
    $clear = @('GALLERY_GITHUB_TOKEN', 'GALLERY_GITHUB_TOKEN_EXPIRES_AT', 'GALLERY_GITHUB_OAUTH_CLIENT_ID',
        'GALLERY_GITHUB_REPO', 'GALLERY_GITHUB_BASE', 'GALLERY_SUBSCRIPTION_ID', 'ARM_SUBSCRIPTION_ID',
        'GALLERY_SETUP_SECRET', 'GALLERY_SETUP_CODE_SHA256', 'GALLERY_SETUP_EXPIRES_AT')
    $authJson = ConvertTo-Json -InputObject $auth -Depth 50 -Compress
    foreach ($name in $clear) {
        if ($authJson -match ('"' + [regex]::Escape($name) + '"')) { throw 'An authentication provider references a reset setting; reconcile that binding manually.' }
    }
    $directory = '/home/data/portal-reset/' + [guid]::NewGuid().ToString('N')
    return [pscustomobject]@{
        AppResourceId = $AppResourceId
        PreviousDatabasePath = $database
        PreviousTerraformPath = $terraform
        NewDatabasePath = "$directory/deployment-portal.db"
        NewTerraformPath = "$directory/tf-runs"
        ClearedSettingNames = $clear
        Image = $Snapshot.Web.properties.linuxFxVersion
    }
}

function Assert-PortalResetSnapshot {
    param([hashtable]$Expected, [hashtable]$Actual)
    $beforeSettings = $Expected.Settings.properties
    $afterSettings = $Actual.Settings.properties
    $keys = @(@($beforeSettings.Keys) + @($afterSettings.Keys) | Sort-Object -Unique)
    foreach ($key in $keys) {
        if (-not $beforeSettings.Contains($key) -or -not $afterSettings.Contains($key) -or [string]$beforeSettings[$key] -cne [string]$afterSettings[$key]) {
            throw 'App settings changed during reset review; no further writes will be attempted.'
        }
    }
    foreach ($name in @('Authentication', 'Web', 'Plan')) {
        $before = ConvertTo-Json -InputObject $Expected[$name].properties -Depth 50 -Compress
        $after = ConvertTo-Json -InputObject $Actual[$name].properties -Depth 50 -Compress
        if ($before -cne $after) { throw 'Configuration changed during reset review; no further writes will be attempted.' }
    }
    $beforeIdentity = $Expected.Site.identity | ConvertTo-Json -Depth 50 -Compress
    $afterIdentity = $Actual.Site.identity | ConvertTo-Json -Depth 50 -Compress
    if ($beforeIdentity -cne $afterIdentity) { throw 'Managed identity changed during reset review.' }
    foreach ($name in @('httpsOnly', 'publicNetworkAccess', 'virtualNetworkSubnetId', 'serverFarmId')) {
        if ($Expected.Site.properties[$name] -cne $Actual.Site.properties[$name]) { throw 'Host configuration changed during reset review.' }
    }
}

function Invoke-PortalReset {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param(
        [string]$SubscriptionId, [string]$ResourceGroupName, [string]$AppName,
        [switch]$AcknowledgeUnmanagedDeployments, [switch]$NonInteractive
    )
    $ErrorActionPreference = 'Stop'
    $SubscriptionId = ConvertTo-PortalGuid (Read-PortalDeploymentInput $SubscriptionId 'Hosting subscription ID' -NonInteractive:$NonInteractive)
    $ResourceGroupName = Read-PortalDeploymentInput $ResourceGroupName 'Existing portal resource group' -NonInteractive:$NonInteractive
    $AppName = Read-PortalDeploymentInput $AppName 'Existing portal App Service name' -NonInteractive:$NonInteractive
    if ($ResourceGroupName -notmatch '^[A-Za-z0-9_().-]{1,90}$' -or $ResourceGroupName.EndsWith('.') -or
        $AppName -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$') { throw 'Use literal resource names.' }
    $target = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/sites/$AppName"
    $snapshot = Get-PortalResetSnapshot $SubscriptionId $target
    $plan = Get-PortalResetPlan $snapshot $target
    Write-PortalSection 'RESET PORTAL | Fresh application state, services retained'
    Write-PortalStatus INFO "Target: $target"
    Write-PortalStatus INFO "Old data retained: $($plan.PreviousDatabasePath); $($plan.PreviousTerraformPath)"
    Write-PortalStatus INFO "New data: $($plan.NewDatabasePath); $($plan.NewTerraformPath)"
    Write-PortalStatus WARN 'Setup, users, catalog, submissions and history will start empty. Existing workloads remain billable and their automatic cleanup stops. Resolve them first or explicitly accept manual cleanup.'
    Write-PortalStatus INFO 'Entra registration/credentials, managed identity, deployed image, all services, GitHub repositories and vault secrets remain. This is not secure erasure or a backup.'
    if (-not $WhatIfPreference) {
        if (-not $AcknowledgeUnmanagedDeployments) { throw 'Apply requires -AcknowledgeUnmanagedDeployments after reviewing workload costs, cleanup and backups.' }
        if ($NonInteractive) {
            if (-not $PSBoundParameters.ContainsKey('Confirm') -or $PSBoundParameters.Confirm) { throw 'Unattended reset requires explicit -Confirm:$false.' }
        } elseif ((Read-Host "Type the exact app name '$AppName' to confirm this reset") -cne $AppName) {
            throw 'Reset cancelled; the app name did not match.'
        }
    }
    if (-not $PSCmdlet.ShouldProcess($target, 'Stop app, detach old data, clear portal connections and start fresh setup; leave all Azure resources intact')) {
        return [pscustomobject]@{ Status='PreviewOnly'; Plan=$plan }
    }
    $fresh = Get-PortalResetSnapshot $SubscriptionId $target
    Assert-PortalResetSnapshot $snapshot $fresh
    if ($fresh.Site.properties.state -ne 'Running') { throw 'App state changed before reset.' }
    $stopped = $false
    try {
        $null = Invoke-PortalAzJson -Arguments @('webapp','stop','--subscription',$SubscriptionId,'--ids',$target) -Operation 'stop portal for reset'
        $stopped = $true
        $fresh = Get-PortalResetSnapshot $SubscriptionId $target
        if ($fresh.Site.properties.state -ne 'Stopped') { throw 'App has not stopped; inspect before retrying.' }
        Assert-PortalResetSnapshot $snapshot $fresh
        $values = @{} + $fresh.Settings.properties
        foreach ($name in $plan.ClearedSettingNames) { $values[$name] = '' }
        $values['GALLERY_DB_PATH'] = $plan.NewDatabasePath
        $values['GALLERY_TF_WORK'] = $plan.NewTerraformPath
        $values['GALLERY_INSTALLER_ENABLED'] = 'false'
        $null = Invoke-PortalArm -Subscription $SubscriptionId -Path "$target/config/appsettings?api-version=2024-11-01" -Method PUT -Body @{properties=$values} -ETag $fresh.Settings.etag
        $observed = Get-PortalResetSnapshot $SubscriptionId $target
        $expected = @{} + $fresh
        $expected.Settings = @{ properties=$values }
        Assert-PortalResetSnapshot $expected $observed
        $null = Invoke-PortalAzJson -Arguments @('webapp','start','--subscription',$SubscriptionId,'--ids',$target) -Operation 'start cleared portal'
        return [pscustomobject]@{
            Status='ResetConfigured'; Plan=$plan; RuntimeVerified=$false
            Next='Verify startup, then sign in using an eligible existing Entra administrator and complete fresh GitHub/subscription Setup. Old resources were not deleted.'
        }
    } catch {
        if ($stopped) { throw 'Reset did not complete. The app was stopped; settings may have changed. Inspect app state and GALLERY_DB_PATH before starting or retrying. Old data and resources were not deleted.' }
        throw 'Reset could not stop the app. Inspect its state before retrying; no reset settings were submitted.'
    } finally {
        $snapshot=$null; $fresh=$null; $observed=$null; $expected=$null; $values=$null
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-PortalReset @resetParameters
}