#Requires -Version 7.0

<#
.SYNOPSIS
Interactively prepares organization Azure hosting prerequisites for Deployment Portal.
.DESCRIPTION
Discovers subscriptions, groups, hosting and organization Entra configuration before
offering reuse or creation. Prompts only for missing names, region and identity
choices, with defaults and numbered selections. Requires confirmation and supports
-WhatIf. Networking is organization-owned and is never provisioned or reconfigured.
.PARAMETER SubscriptionId
Organization Azure public-cloud subscription. Interactive discovery reads account show
first and offers only subscriptions for that login identity and tenant. Enter uses
its current subscription; cached defaults from other logins are ignored.
.PARAMETER ResourceGroupName
Resource group to create or reuse. Omission offers existing groups or a new name;
services across the subscription remain available. Selecting an app in another
group requires confirmation to use that app's original group without moving it.
.PARAMETER Location
Azure region for new resources. Existing app/plan regions are inherited; interactive
input accepts canonical names or display names such as centralus or Central US.
.PARAMETER AppName
Web app name to create or reuse. Existing Linux apps are offered when omitted.
.PARAMETER PlanName
Linux App Service plan name to create or reuse. Inherited from a selected existing app.
.PARAMETER RegistryName
Organization ACR name to create or reuse. Existing registries are offered; missing ones
receive an editable, availability-checked name. New registries use Basic.
.PARAMETER PlanResourceId
Full resource ID of a selected existing plan, including another group in the same
subscription. The app and plan must share a region. Inferred by interactive discovery.
.PARAMETER RegistryResourceId
Full resource ID of a selected existing ACR in the same subscription. It need not
share the hosting group or region. Inferred by interactive discovery.
.PARAMETER ManagedIdentityResourceId
Existing user-assigned identity in the selected subscription to reuse and attach.
Configured host identities are retained; no existing identity is removed.
.PARAMETER NewManagedIdentityName
Create a named user-assigned identity in the hosting group instead of using a
system identity. Mutually exclusive with ManagedIdentityResourceId.
.PARAMETER PlanSku
App Service SKU for new plans only. Does not itself resize existing plans.
Accepts F1 (Free, without Always On), B1 (default), B2, B3, S1 or P1v3.
.PARAMETER UpgradePlanSku
Explicit paid target for an existing F1/D1 plan (B1/B2/B3/S1/P1v3). The wizard lists
shared apps and separately confirms the cost-changing upgrade. Other paid tiers
are not resized. Always On is enabled and verified on supported paid tiers;
keeping F1/D1 produces a warning without blocking preparation or deployment.
.PARAMETER RepairEntra
Include repairable existing-Entra findings in the plan: missing exact callback,
ID-token issuance, assignment-required policy and initial member-admin app access.
Repairs require separate confirmation on apply; no repair occurs in WhatIf.
When a new host initially has PendingHost readiness, the wizard rechecks its
actual hostname after provisioning and offers the same approved repair flow
before connecting the existing registration. Unattended runs require this switch.
.PARAMETER AdminObjectIds
Initial administrator user object IDs in the organization tenant. Existing host values
are retained; otherwise an enabled signed-in member may be suggested for confirmation.
.PARAMETER EntraClientId
Optional existing organization app registration to connect after hosting is prepared.
Reuse validates configuration and operator directory reads before writes. The
console lists missing/unverified items or reports Entra registration correctly
configured when all checks pass. WhatIf also returns the safe EntraChecks list.
If the host lacks a stored client secret, interactive apply explains where to find
its Value and requests masked terminal entry. It saves the credential in protected
App Service settings, not local files or the saved Prepare configuration.
.PARAMETER EntraMode
Create an organization registration, connect Existing, or configure Later. Interactive
runs prompt when omitted; unattended runs must choose explicitly.
.PARAMETER EntraDisplayName
Display name for a new dedicated organization registration; prompted in Create mode.
.PARAMETER CredentialDays
Generated Entra credential lifetime, 7-180 days; default 90. Schedule rotation.
.PARAMETER NonInteractive
Require all inputs; apply additionally requires explicit -Confirm:$false.
.PARAMETER ConfigPath
Local JSON selections file. Defaults to .portal/prepare.json beside the tools folder.
An eligible WhatIf saves it; a later Prepare loads it unless explicit selections are supplied.
Only non-secret selections are saved, not credentials, repair approvals or confirmation flags.
.PARAMETER Fresh
Ignore saved selections and run discovery again. A successful WhatIf replaces the file.
.EXAMPLE
./tools/Initialize-Portal.ps1 -WhatIf
.NOTES
Run as an operator authorized to provision hosting, attach selected identities and assign roles.
App Service and ACR are billable. Directory creation has separate permissions.
No user sign-in is performed. Organization network restrictions are retained.
WhatIf includes read-only Entra readiness and returns ReadyForApply. Runtime and
employee sign-in cannot be certified by preview; deployment reports a health observation.
Read-only Entra validation does not prove repair-write permissions or secret validity.
Resource creation/reuse waits up to ten minutes for in-progress provisioning and
verifies completion. Timeout or failure stops without recreating or deleting resources.
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [string]$SubscriptionId,
    [string]$ResourceGroupName,
    [string]$Location,
    [string]$AppName,
    [string]$PlanName,
    [string]$RegistryName,
    [string]$PlanResourceId,
    [string]$RegistryResourceId,
    [string]$ManagedIdentityResourceId,
    [string]$NewManagedIdentityName,
    [ValidateSet('B1','B2','B3','S1','P1v3')][string]$UpgradePlanSku,
    [switch]$RepairEntra,
    [ValidateSet('F1', 'B1', 'B2', 'B3', 'S1', 'P1v3')][string]$PlanSku = 'B1',
    [string[]]$AdminObjectIds,
    [string]$EntraClientId,
    [ValidateSet('Create','Existing','Later')][string]$EntraMode,
    [string]$EntraDisplayName,
    [ValidateRange(7,180)][int]$CredentialDays = 90,
    [string]$ConfigPath,
    [switch]$Fresh,
    [switch]$NonInteractive
)

$initializerParameters = @{} + $PSBoundParameters
. "$PSScriptRoot/Deploy-Portal.ps1"
. "$PSScriptRoot/Portal-Foundation.ps1"

function Get-PortalPrepareSelectionName {
    return @('SubscriptionId','ResourceGroupName','Location','AppName','PlanName','RegistryName',
        'PlanResourceId','RegistryResourceId','ManagedIdentityResourceId','NewManagedIdentityName',
        'UpgradePlanSku','PlanSku','AdminObjectIds','EntraClientId','EntraMode','EntraDisplayName','CredentialDays')
}

function Import-PortalPrepareConfiguration {
    param([string]$Path)
    try {
        if ((Get-Item -LiteralPath $Path -ErrorAction Stop).Length -gt 65536) { throw 'Oversized configuration' }
        $configuration = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json -AsHashtable -ErrorAction Stop
        if ($configuration -isnot [System.Collections.IDictionary] -or $configuration.Count -ne 3 -or
            $configuration.SchemaVersion -cne 1 -or -not $configuration.Contains('TenantId') -or
            -not $configuration.Contains('Selections')) { throw 'Invalid schema' }
        $configuration.TenantId = ConvertTo-PortalGuid $configuration.TenantId
        $selections = $configuration.Selections
        $names = @(Get-PortalPrepareSelectionName)
        if ($selections -isnot [System.Collections.IDictionary] -or $selections.Count -ne $names.Count) { throw 'Invalid selections' }
        foreach ($name in $names) {
            if (-not $selections.Contains($name)) { throw 'Missing selection' }
            $value = $selections[$name]
            if ($name -eq 'AdminObjectIds') {
                if ($value -isnot [array] -or $value.Count -eq 0) { throw 'Invalid administrators' }
                foreach ($administrator in $value) { $null = ConvertTo-PortalGuid $administrator }
            } elseif ($name -eq 'CredentialDays') {
                if ($value -isnot [long] -and $value -isnot [int]) { throw 'Invalid lifetime' }
                if ($value -lt 7 -or $value -gt 180) { throw 'Invalid lifetime' }
            } elseif ($value -isnot [string] -or $value.Length -gt 2048 -or $value -match '[\x00-\x1f\x7f]') {
                throw 'Invalid selection value'
            }
        }
        foreach ($name in @('SubscriptionId','ResourceGroupName','Location','AppName','PlanName','RegistryName')) {
            if ([string]::IsNullOrWhiteSpace($selections[$name])) { throw 'Missing required selection' }
        }
        $null = ConvertTo-PortalGuid $selections.SubscriptionId
        if ($selections.PlanSku -notin @('F1','B1','B2','B3','S1','P1v3') -or
            ($selections.UpgradePlanSku -and $selections.UpgradePlanSku -notin @('B1','B2','B3','S1','P1v3')) -or
            $selections.EntraMode -notin @('Create','Existing')) { throw 'Invalid mode or SKU' }
        if ($selections.EntraMode -eq 'Existing') { $null = ConvertTo-PortalGuid $selections.EntraClientId }
        if ($selections.EntraMode -eq 'Create' -and [string]::IsNullOrWhiteSpace($selections.EntraDisplayName)) { throw 'Missing registration name' }
        return $configuration
    } catch {
        throw 'Prepare configuration is invalid or unreadable. Use -Fresh to make new selections and generate a valid preview configuration. File contents have not been executed.'
    }
}

function Export-PortalPrepareConfiguration {
    param([string]$Path, [string]$TenantId, [hashtable]$Selections)
    $safeSelections = [ordered]@{}
    foreach ($name in Get-PortalPrepareSelectionName) {
        $safeSelections[$name] = switch ($name) {
            'AdminObjectIds' { ,@($Selections[$name]) }
            'CredentialDays' { [int]$Selections[$name] }
            default { [string]$Selections[$name] }
        }
    }
    $configuration = @{ SchemaVersion=1; TenantId=$TenantId; Selections=$safeSelections }
    $fullPath = [IO.Path]::GetFullPath($Path)
    $temporaryPath = "$fullPath.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($fullPath))
        [IO.File]::WriteAllText($temporaryPath, ($configuration | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
        $null = Import-PortalPrepareConfiguration $temporaryPath
        [IO.File]::Move($temporaryPath, $fullPath, $true)
    } finally {
        if ([IO.File]::Exists($temporaryPath)) { [IO.File]::Delete($temporaryPath) }
    }
}

function Get-PortalHostingResourceId {
    param([hashtable]$Inputs, [ValidateSet('Plan','Registry')][string]$Kind)
    $resourceType = if ($Kind -eq 'Plan') { 'Microsoft.Web/serverfarms' } else { 'Microsoft.ContainerRegistry/registries' }
    $name = $Inputs["${Kind}Name"]
    $resourceId = [string]$Inputs["${Kind}ResourceId"]
    if (-not $resourceId) { return "/subscriptions/$($Inputs.SubscriptionId)/resourceGroups/$($Inputs.ResourceGroupName)/providers/$resourceType/$name" }
    $pattern = '^/subscriptions/' + [regex]::Escape($Inputs.SubscriptionId) + '/resourceGroups/[A-Za-z0-9_.()-]+/providers/' + [regex]::Escape($resourceType) + '/' + [regex]::Escape($name) + '$'
    if ($resourceId -notmatch $pattern) { throw "The selected $Kind resource ID must match its name and deployment subscription." }
    return $resourceId
}

function Get-PortalManagedIdentityId {
    param([hashtable]$Inputs)
    if ($Inputs.ManagedIdentityResourceId -and $Inputs.NewManagedIdentityName) { throw 'Choose an existing managed identity or a new identity name, not both.' }
    if ($Inputs.NewManagedIdentityName) {
        if ($Inputs.NewManagedIdentityName -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$') { throw 'Invalid new managed identity name.' }
        return "/subscriptions/$($Inputs.SubscriptionId)/resourceGroups/$($Inputs.ResourceGroupName)/providers/Microsoft.ManagedIdentity/userAssignedIdentities/$($Inputs.NewManagedIdentityName)"
    }
    $identityId = [string]$Inputs.ManagedIdentityResourceId
    $pattern = '^/subscriptions/' + [regex]::Escape($Inputs.SubscriptionId) + '/resourceGroups/[A-Za-z0-9_.()-]+/providers/Microsoft.ManagedIdentity/userAssignedIdentities/[A-Za-z0-9_-]+$'
    if ($identityId -and $identityId -notmatch $pattern) { throw 'Managed identity must be a user-assigned identity in the selected subscription.' }
    return $identityId
}

function Get-PortalPrerequisitePlan {
    param([hashtable]$Inputs, [hashtable]$Snapshot)
    $subscription = ConvertTo-PortalGuid $Inputs.SubscriptionId
    if ($Snapshot.Account.id -ne $subscription -or $Snapshot.Account.state -ne 'Enabled' -or $Snapshot.Account.environmentName -ne 'AzureCloud') {
        throw 'Choose an enabled Azure public-cloud subscription.'
    }
    $tenant = ConvertTo-PortalGuid $Snapshot.Account.tenantId
    foreach ($name in @('ResourceGroupName', 'AppName', 'PlanName')) {
        if ($Inputs[$name] -notmatch '^[a-zA-Z0-9][a-zA-Z0-9-]{0,58}[a-zA-Z0-9]$') { throw "Invalid $name. Use 2-60 alphanumeric/hyphen characters." }
    }
    if ($Inputs.RegistryName -notmatch '^[a-z0-9]{5,50}$' -or $Inputs.Location -notmatch '^[a-z][a-z0-9]+$') {
        throw 'Use an alphanumeric registry name and an Azure region name.'
    }
    if ($Inputs.PlanSku -notin @('F1', 'B1', 'B2', 'B3', 'S1', 'P1v3')) { throw 'Unsupported new App Service plan SKU.' }
    $admins = @($Inputs.AdminObjectIds | ForEach-Object { ConvertTo-PortalGuid $_ } | Sort-Object -Unique)
    if ($admins.Count -eq 0) { throw 'Specify the intended administrator user object ID.' }
    $groupId = "/subscriptions/$subscription/resourceGroups/$($Inputs.ResourceGroupName)"
    $planId = Get-PortalHostingResourceId $Inputs Plan
    $appId = "$groupId/providers/Microsoft.Web/sites/$($Inputs.AppName)"
    $registryId = Get-PortalHostingResourceId $Inputs Registry
    $identityId = Get-PortalManagedIdentityId $Inputs
    if ($Inputs.ManagedIdentityResourceId -and -not $Snapshot.ManagedIdentity) { throw 'The selected managed identity is unavailable; it will not be recreated.' }
    if ($Snapshot.ManagedIdentity -and ($Snapshot.ManagedIdentity.id -ne $identityId -or
        -not $Snapshot.ManagedIdentity.properties.clientId -or -not $Snapshot.ManagedIdentity.properties.principalId)) { throw 'Managed identity metadata does not match the selected resource.' }
    if ($identityId -and $Snapshot.Settings.properties.AZURE_CLIENT_ID -and $Snapshot.Settings.properties.AZURE_CLIENT_ID -ne $Snapshot.ManagedIdentity.properties.clientId) { throw 'The configured host identity will not be replaced.' }
    if ($identityId -and $Snapshot.Settings.properties.GALLERY_MANAGED_IDENTITY -eq 'system') { throw 'The configured system host identity will not be replaced.' }
    foreach ($pair in @(@('Plan', $planId), @('Site', $appId), @('Registry', $registryId))) {
        $existing = $Snapshot[$pair[0]]
        if ($existing -and ($existing.id -ne $pair[1] -or ($pair[0] -ne 'Registry' -and ($existing.location -replace '\s','') -ne $Inputs.Location))) {
            throw 'An existing resource does not match the requested target or region.'
        }
    }
    foreach ($kind in @('Plan','Registry')) {
        if ($Inputs["${kind}ResourceId"] -and -not $Snapshot[$kind]) { throw "The selected existing $kind could not be found. It will not be recreated." }
    }
    if ($Snapshot.Plan -and ($Snapshot.Plan.properties.reserved -ne $true -or $Snapshot.Plan.properties.numberOfWorkers -ne 1 -or
        $Snapshot.Plan.properties.elasticScaleEnabled -eq $true)) { throw 'Reuse only a single-instance Linux plan without automatic scaling.' }
    if ($Snapshot.Registry.properties.adminUserEnabled -eq $true) { throw 'Disable the existing registry admin account before continuing.' }
    if ($Snapshot.Site -and ($Snapshot.Site.kind -notmatch 'linux' -or $Snapshot.Site.properties.serverFarmId -ne $planId)) {
        throw 'Existing web app must be Linux and on the selected plan; this workflow never moves apps between plans.'
    }
    if ($Snapshot.Web.properties.linuxFxVersion -like 'sitecontainers*') { throw 'Sidecar-enabled existing apps require their own container workflow.' }
    $settings = $Snapshot.Settings.properties
    if ($settings.GALLERY_INSTALLER_ENABLED -in @('true','yes','1')) { throw 'Complete or retire the existing code installer first.' }
    $previousAdmins = @(([string]$settings.GALLERY_ADMIN_OIDS).Split(',', [StringSplitOptions]::RemoveEmptyEntries) | ForEach-Object { ConvertTo-PortalGuid $_.Trim() } | Sort-Object -Unique) -join ','
    if ($previousAdmins -and $previousAdmins -ne ($admins -join ',')) { throw 'Existing administrator eligibility differs; it will not be replaced.' }
    $hostingReadiness = Get-PortalHostingReadiness -Plan $Snapshot.Plan -Web $Snapshot.Web -NewPlanSku $Inputs.PlanSku -UpgradePlanSku $Inputs.UpgradePlanSku
    $steps = @(
        @{ Resource = $Inputs.ResourceGroupName; Action = $(if ($Snapshot.Group) { 'Reuse group' } else { 'Create group' }) },
        @{ Resource = $planId; Action = $(if ($hostingReadiness.UpgradeRequired) { $hostingReadiness.Detail } elseif ($Snapshot.Plan) { 'Reuse Linux plan unchanged' } else { "Create single-instance Linux $($Inputs.PlanSku) plan" }) },
        @{ Resource = $registryId; Action = $(if ($Snapshot.Registry) { 'Reuse ACR and its network policy' } else { 'Create Basic ACR with authenticated image access (billable)' }) },
        @{ Resource = $Inputs.AppName; Action = $(if ($Snapshot.Site) { 'Configure selected existing web app; retain networking' } else { 'Create HTTPS Linux web app with system identity; stop until configured' }) },
        @{ Resource = $appId; Action = 'Require authentication, HTTPS, persistent storage and explicit administrator IDs; enable Always On only on supported paid tiers' },
        @{ Resource = $appId; Action = 'Grant host Reader (subscription), Website Contributor (app), and image pull (registry)' }
    )
    if ($identityId) {
        $steps += @{ Resource=$identityId; Action=$(if ($Snapshot.ManagedIdentity) { 'Reuse and attach selected managed identity; preserve other attachments' } else { 'Create and attach named managed identity' }) }
    }
    if ($hostingReadiness.UpgradeRequired) {
        foreach ($sharedApp in @($Snapshot.PlanSites.value | Where-Object { $_ })) {
            $steps += @{ Resource=$sharedApp.id; Action='Shares the plan being upgraded; review cost and impact before confirmation' }
        }
    }
    foreach ($provider in @($Snapshot.Providers | Where-Object { $null -ne $_ -and $_.registrationState -ne 'Registered' })) {
        $steps = @(@{ Resource = $provider.namespace; Action = 'Register required resource provider in selected subscription' }) + $steps
    }
    return [pscustomobject]@{
        SubscriptionId = $subscription; TenantId = $tenant; GroupId = $groupId
        AppResourceId = $appId; PlanId = $planId; RegistryId = $registryId; ManagedIdentityId = $identityId
        Inputs = $Inputs; AdministratorIds = $admins -join ','; Steps = $steps; HostingReadiness = $hostingReadiness
        SharedApps = $Snapshot.PlanSites
    }
}

function Invoke-PortalPrerequisiteResource {
    param(
        [string]$Subscription, [string]$ResourceId, [string]$ApiVersion,
        [ValidateSet('GET', 'PUT', 'PATCH')][string]$Method = 'GET',
        [hashtable]$Body, [switch]$AllowMissing, [switch]$CreateOnly
    )
    $prefix = "/subscriptions/$Subscription/resourceGroups/"
    $locationRead = $Method -eq 'GET' -and $ResourceId -eq "/subscriptions/$Subscription/locations"
    if ((-not $locationRead -and (-not $ResourceId.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or
        $ResourceId -match '[?#%\\\s]' -or $ResourceId.Contains('/../') -or
        $ResourceId -notmatch '/resourceGroups/[A-Za-z0-9_.()-]+(?:/providers/Microsoft\.(?:Web|ContainerRegistry)/[A-Za-z0-9_./-]+|/providers/Microsoft.ManagedIdentity/userAssignedIdentities/[A-Za-z0-9_-]+)?$')) -or
        $ApiVersion -notmatch '^20\d{2}-\d{2}-\d{2}$') { throw 'Unexpected prerequisite resource destination.' }
    $token = Invoke-PortalAzJson -Arguments @('account', 'get-access-token', '--subscription', $Subscription, '--resource', 'https://management.azure.com/') -Operation 'prerequisite authorization'
    $headers = @{ Authorization = "Bearer $($token.accessToken)" }
    if ($CreateOnly) { $headers['If-None-Match'] = '*' }
    $parameters = @{
        Uri = "https://management.azure.com$ResourceId`?api-version=$ApiVersion"
        Method = $Method; Headers = $headers; TimeoutSec = 120; MaximumRedirection = 0; ErrorAction = 'Stop'
    }
    if ($null -ne $Body) { $parameters.ContentType = 'application/json'; $parameters.Body = ConvertTo-Json -InputObject $Body -Depth 40 -Compress }
    try {
        $response = Invoke-RestMethod @parameters
        if ($null -eq $response) { return @{} }
        return $response | ConvertTo-Json -Depth 40 | ConvertFrom-Json -AsHashtable
    } catch {
        if ($AllowMissing -and $Method -eq 'GET' -and [int]$_.Exception.Response.StatusCode -eq 404) { return $null }
        throw "Prerequisite $Method failed for $ResourceId. Remote body withheld. Inspect Azure before retrying; no automatic rollback is performed."
    } finally { $token = $null; $parameters = $null; $headers = $null; $response = $null }
}

function Get-PortalPrerequisiteSnapshot {
    param([hashtable]$Inputs)
    $subscription = $Inputs.SubscriptionId
    $group = "/subscriptions/$subscription/resourceGroups/$($Inputs.ResourceGroupName)"
    $appId = "$group/providers/Microsoft.Web/sites/$($Inputs.AppName)"
    $site = Invoke-PortalPrerequisiteResource $subscription $appId '2024-11-01' -AllowMissing
    $identityId = Get-PortalManagedIdentityId $Inputs
    return @{
        Account = Invoke-PortalAzJson -Arguments @('account', 'show', '--subscription', $subscription) -Operation 'selected subscription'
        Providers = @(foreach ($namespace in @('Microsoft.Web', 'Microsoft.ContainerRegistry') + $(if ($Inputs.NewManagedIdentityName) { @('Microsoft.ManagedIdentity') } else { @() })) {
            Invoke-PortalAzJson -Arguments @('provider', 'show', '--subscription', $subscription, '--namespace', $namespace) -Operation 'resource provider registration state'
        })
        Group = Invoke-PortalPrerequisiteResource $subscription $group '2022-09-01' -AllowMissing
        Plan = Invoke-PortalPrerequisiteResource $subscription (Get-PortalHostingResourceId $Inputs Plan) '2024-11-01' -AllowMissing
        Site = $site
        Web = $(if ($site) { Invoke-PortalArm -Subscription $subscription -Path "$appId/config/web?api-version=2024-11-01" } else { @{} })
        Settings = $(if ($site) { Invoke-PortalArm -Subscription $subscription -Path "$appId/config/appsettings/list?api-version=2024-11-01" -Method POST } else { @{} })
        Authentication = $(if ($site) { Invoke-PortalArm -Subscription $subscription -Path "$appId/config/authsettingsV2?api-version=2024-11-01" } else { @{} })
        Registry = Invoke-PortalPrerequisiteResource $subscription (Get-PortalHostingResourceId $Inputs Registry) '2023-07-01' -AllowMissing
        ManagedIdentity = $(if ($identityId) { Invoke-PortalPrerequisiteResource $subscription $identityId '2023-01-31' -AllowMissing } else { $null })
        PlanSites = $(if ($Inputs.UpgradePlanSku) { Invoke-PortalPrerequisiteResource $subscription "$(Get-PortalHostingResourceId $Inputs Plan)/sites" '2024-11-01' } else { $null })
    }
}

function Set-PortalPlanUpgrade {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param([object]$Plan, [hashtable]$Snapshot)
    if (-not $Plan.HostingReadiness.UpgradeRequired) { return $true }
    if (-not $Snapshot.PlanSites -or $Snapshot.PlanSites.'nextLink' -or $Snapshot.PlanSites.'@odata.nextLink' -or -not $Snapshot.PlanSites.Contains('value')) { throw 'The full shared-plan app inventory is required before upgrading.' }
    $targetSku = $Plan.HostingReadiness.TargetSku
    Write-PortalStatus WARN "Paid plan upgrade: $($Plan.HostingReadiness.CurrentSku) -> $targetSku. This affects every app on $($Plan.PlanId). Review Azure pricing and budget."
    if (-not $PSCmdlet.ShouldProcess($Plan.PlanId, "Upgrade shared plan to paid $targetSku with one instance")) { return $false }
    $current = Invoke-PortalPrerequisiteResource $Plan.SubscriptionId $Plan.PlanId '2024-11-01'
    $apps = Invoke-PortalPrerequisiteResource $Plan.SubscriptionId "$($Plan.PlanId)/sites" '2024-11-01'
    if ($current.sku.name -ne $Plan.HostingReadiness.CurrentSku -or $current.properties.numberOfWorkers -ne 1 -or
        $current.properties.elasticScaleEnabled -eq $true -or $apps.nextLink -or $apps.'@odata.nextLink' -or
        (@($apps.value.id | Sort-Object) -join ',') -cne (@($Snapshot.PlanSites.value.id | Sort-Object) -join ',')) {
        throw 'The shared plan changed after preview. Review its current SKU, instance count and apps before retrying.'
    }
    $tier = if ($targetSku.StartsWith('B')) { 'Basic' } elseif ($targetSku.StartsWith('S')) { 'Standard' } else { 'PremiumV3' }
    $null = Invoke-PortalPrerequisiteResource $Plan.SubscriptionId $Plan.PlanId '2024-11-01' -Method PATCH -Body @{ sku=@{ name=$targetSku; tier=$tier; capacity=1 } }
    $observed = Invoke-PortalPrerequisiteResource $Plan.SubscriptionId $Plan.PlanId '2024-11-01'
    if ($observed.sku.name -ne $targetSku -or $observed.properties.numberOfWorkers -ne 1) { throw 'Plan upgrade is not yet verified. Inspect Azure and rerun after provisioning completes; no rollback is attempted.' }
    return $true
}

function Enable-PortalAlwaysOn {
    param([object]$Plan)
    $hostingPlan = Invoke-PortalPrerequisiteResource $Plan.SubscriptionId $Plan.PlanId '2024-11-01'
    $readiness = Get-PortalHostingReadiness -Plan $hostingPlan -Web @{}
    if ($readiness.Status -eq 'Blocked') { throw $readiness.Detail }
    if ($readiness.Status -eq 'Warning') {
        Write-Verbose $readiness.Detail
        return
    }
    $path = "$($Plan.AppResourceId)/config/web?api-version=2024-11-01"
    $web = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path $path
    if ($web.properties.alwaysOn -ne $true) {
        $properties = @{} + $web.properties
        $properties.alwaysOn = $true
        $null = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path $path -Method PUT -Body @{ properties=$properties } -ETag $web.etag
    }
    $observed = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path $path
    if ($observed.properties.alwaysOn -ne $true) { throw 'Always On readback failed; background-job readiness has not been established.' }
}

function New-PortalPrerequisiteResource {
    param([string]$Subscription, [string]$ResourceId, [string]$ApiVersion, [hashtable]$Body, [switch]$RequireExisting)
    $result = Invoke-PortalPrerequisiteResource $Subscription $ResourceId $ApiVersion -AllowMissing
    if (-not $result) {
        if ($RequireExisting) { throw "Selected existing resource disappeared: $ResourceId. It will not be recreated." }
        $result = Invoke-PortalPrerequisiteResource $Subscription $ResourceId $ApiVersion -Method PUT -Body $Body -CreateOnly
    }
    $state = [string]$result.properties.provisioningState
    if ($state -in @('Accepted','Creating','Updating','Running')) {
        Write-PortalStatus INFO "Waiting for Azure provisioning ($state): $ResourceId. Timeout: 10 minutes; no additional create request will be sent."
        try {
            $null = Invoke-PortalAzJson -Arguments @('resource','wait','--subscription',$Subscription,'--ids',$ResourceId,
                '--api-version',$ApiVersion,'--created','--interval','10','--timeout','600') -Operation 'wait for prerequisite provisioning'
        } catch {
            throw "Provisioning wait timed out or failed for $ResourceId. Inspect its Azure provisioning state before rerunning; no resource was recreated or deleted. Remote output withheld."
        }
        $result = Invoke-PortalPrerequisiteResource $Subscription $ResourceId $ApiVersion
        $state = [string]$result.properties.provisioningState
        if ($state -ne 'Succeeded') {
            throw "Provisioning completion could not be verified for $ResourceId (state: '$state'). Inspect Azure before rerunning; no resource was recreated or deleted."
        }
    }
    if ($state -and $state -ne 'Succeeded') {
        throw "Azure reports '$state' for $ResourceId. Inspect the provisioning failure or operation before rerunning; no resource was recreated or deleted."
    }
    return $result
}

function Grant-PortalHostRole {
    param([string]$Subscription, [string]$PrincipalId, [string]$Scope, [string]$Role)
    if ($Role -notin @('Reader', 'Website Contributor', 'AcrPull', 'Container Registry Repository Reader')) { throw 'Unapproved host role.' }
    $roles = @(Invoke-PortalAzJson -Arguments @('role', 'definition', 'list', '--subscription', $Subscription, '--name', $Role) -Operation 'role definition lookup')
    if ($roles.Count -ne 1) { throw 'Role definition could not be resolved uniquely.' }
    $assignments = @(Invoke-PortalAzJson -Arguments @('role', 'assignment', 'list', '--subscription', $Subscription,
        '--scope', $Scope, '--include-inherited', '--assignee-object-id', $PrincipalId,
        '--fill-principal-name', 'false', '--fill-role-definition-name', 'false') -Operation 'existing role assignments')
    if (@($assignments | Where-Object { $_.principalId -eq $PrincipalId -and $_.roleDefinitionId -eq $roles[0].id }).Count -eq 0) {
        $null = Invoke-PortalAzJson -Arguments @('role', 'assignment', 'create', '--subscription', $Subscription,
            '--assignee-object-id', $PrincipalId, '--assignee-principal-type', 'ServicePrincipal', '--scope', $Scope, '--role', $Role) -Operation 'scoped host role assignment'
    }
}

function Set-PortalHostIdentity {
    param([object]$Plan, [hashtable]$Site, [hashtable]$Settings)
    $subscription = $Plan.SubscriptionId
    $clientId = [string]$Settings.AZURE_CLIENT_ID
    if ($Plan.ManagedIdentityId) {
        $selected = New-PortalPrerequisiteResource $subscription $Plan.ManagedIdentityId '2023-01-31' -RequireExisting:([bool]$Plan.Inputs.ManagedIdentityResourceId) -Body @{ location=$Plan.Inputs.Location }
        $selectedClient = ConvertTo-PortalGuid $selected.properties.clientId
        $principal = ConvertTo-PortalGuid $selected.properties.principalId
        if (($clientId -and $clientId -ne $selectedClient) -or $Settings.GALLERY_MANAGED_IDENTITY -eq 'system') { throw 'Existing host identity configuration changed; it will not be replaced.' }
        $attachments = @{}
        foreach ($existingId in @($Site.identity.userAssignedIdentities.Keys | Where-Object { $_ })) { $attachments[$existingId] = @{} }
        $attachments[$Plan.ManagedIdentityId] = @{}
        $existingAttachment = if ($Site.identity.userAssignedIdentities) { $Site.identity.userAssignedIdentities[$Plan.ManagedIdentityId] } else { $null }
        if ($existingAttachment.clientId -ne $selectedClient) {
            $identityType = if ($Site.identity.type -match 'SystemAssigned') { 'SystemAssigned, UserAssigned' } else { 'UserAssigned' }
            $null = Invoke-PortalPrerequisiteResource $subscription $Plan.AppResourceId '2024-11-01' -Method PATCH -Body @{
                identity=@{ type=$identityType; userAssignedIdentities=$attachments }
            }
            $observed = Invoke-PortalPrerequisiteResource $subscription $Plan.AppResourceId '2024-11-01'
            if (-not $observed.identity.userAssignedIdentities -or $observed.identity.userAssignedIdentities[$Plan.ManagedIdentityId].clientId -ne $selectedClient) { throw 'Selected identity attachment has not converged. Inspect Azure and rerun.' }
        }
        return @{ Mode='user'; ClientId=$selectedClient; PrincipalId=$principal }
    }
    if ($clientId) {
        $attached = @($Site.identity.userAssignedIdentities.Values | Where-Object { $_.clientId -eq $clientId })
        if ($attached.Count -ne 1) { throw 'The configured user-assigned identity is not attached.' }
        return @{ Mode='user'; ClientId=$clientId; PrincipalId=(ConvertTo-PortalGuid $attached[0].principalId) }
    }
    if (-not $Site.identity.principalId) {
        $null = Invoke-PortalAzJson -Arguments @('webapp','identity','assign','--subscription',$subscription,'--resource-group',$Plan.Inputs.ResourceGroupName,'--name',$Plan.Inputs.AppName) -Operation 'enable system-assigned host identity'
        $Site = Invoke-PortalPrerequisiteResource $subscription $Plan.AppResourceId '2024-11-01'
    }
    return @{ Mode='system'; ClientId=''; PrincipalId=(ConvertTo-PortalGuid $Site.identity.principalId) }
}

function Set-PortalPrerequisites {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param([object]$Plan)
    if (-not $PSCmdlet.ShouldProcess($Plan.GroupId, 'Create/reuse billable hosting and authenticated registry; configure selected app and host roles; retain organization networking')) {
        return [pscustomobject]@{ Status = 'PreviewOnly'; AppResourceId = $Plan.AppResourceId }
    }
    $subscription = $Plan.SubscriptionId
    $inputs = $Plan.Inputs
    $snapshot = Get-PortalPrerequisiteSnapshot $inputs
    $freshPlan = Get-PortalPrerequisitePlan $inputs $snapshot
    if ($freshPlan.HostingReadiness.Status -eq 'Blocked') { throw $freshPlan.HostingReadiness.Detail }
    if ($Plan.HostingReadiness.UpgradeRequired -and -not (Set-PortalPlanUpgrade -Plan $Plan -Snapshot @{ PlanSites=$Plan.SharedApps })) {
        return [pscustomobject]@{ Status='Cancelled'; AppResourceId=$Plan.AppResourceId }
    }
    foreach ($provider in @($snapshot.Providers | Where-Object { $null -ne $_ -and $_.registrationState -ne 'Registered' })) {
        if ($provider.namespace -notin @('Microsoft.Web', 'Microsoft.ContainerRegistry') -and
            -not ($provider.namespace -eq 'Microsoft.ManagedIdentity' -and $inputs.NewManagedIdentityName)) { throw 'Unexpected resource provider registration.' }
        $null = Invoke-PortalAzJson -Arguments @('provider', 'register', '--subscription', $subscription, '--namespace', $provider.namespace, '--wait') -Operation 'required provider registration'
    }
    $null = New-PortalPrerequisiteResource $subscription $Plan.GroupId '2022-09-01' @{ location = $inputs.Location }
    $tier = if ($inputs.PlanSku -eq 'F1') { 'Free' } elseif ($inputs.PlanSku.StartsWith('B')) { 'Basic' } elseif ($inputs.PlanSku.StartsWith('S')) { 'Standard' } else { 'PremiumV3' }
    $null = New-PortalPrerequisiteResource $subscription $Plan.PlanId '2024-11-01' -RequireExisting:([bool]$inputs.PlanResourceId) -Body @{
        location = $inputs.Location; kind = 'linux'; sku = @{ name = $inputs.PlanSku; tier = $tier; capacity = 1 }
        properties = @{ reserved = $true; numberOfWorkers = 1 }
    }
    $registry = New-PortalPrerequisiteResource $subscription $Plan.RegistryId '2023-07-01' -RequireExisting:([bool]$inputs.RegistryResourceId) -Body @{
        location = $inputs.Location; sku = @{ name = 'Basic' }
        properties = @{ adminUserEnabled = $false; anonymousPullEnabled = $false }
    }
    $site = New-PortalPrerequisiteResource $subscription $Plan.AppResourceId '2024-11-01' @{
        location = $inputs.Location; kind = 'app,linux'; identity = @{ type = 'SystemAssigned' }
        properties = @{ serverFarmId = $Plan.PlanId; httpsOnly = $true
            siteConfig = @{ linuxFxVersion = 'PYTHON|3.12'; minTlsVersion = '1.2'; ftpsState = 'Disabled'; alwaysOn = (Test-PortalAlwaysOnSku $freshPlan.HostingReadiness.TargetSku) } }
    }
    if (-not $snapshot.Site) {
        $null = Invoke-PortalAzJson -Arguments @('webapp', 'stop', '--subscription', $subscription, '--ids', $Plan.AppResourceId) -Operation 'stop newly created host pending authenticated image deployment'
    }
    $settingsPath = "$($Plan.AppResourceId)/config/appsettings"
    $settings = Invoke-PortalArm -Subscription $subscription -Path "$settingsPath/list?api-version=2024-11-01" -Method POST
    $values = $settings.properties
    $hostIdentity = Set-PortalHostIdentity -Plan $Plan -Site $site -Settings $values
    $identityMode = $hostIdentity.Mode
    $principal = $hostIdentity.PrincipalId
    $currentAdmins = @(([string]$values.GALLERY_ADMIN_OIDS).Split(',', [StringSplitOptions]::RemoveEmptyEntries) | ForEach-Object { ConvertTo-PortalGuid $_.Trim() } | Sort-Object -Unique) -join ','
    if ($currentAdmins -and $currentAdmins -ne $Plan.AdministratorIds) { throw 'Existing administrator eligibility differs. It will not be replaced.' }
    if ($values.GALLERY_INSTALLER_ENABLED -in @('true','yes','1')) { throw 'An active code installer must be completed or retired before this workflow.' }
    $null = Invoke-PortalPrerequisiteResource $subscription $Plan.AppResourceId '2024-11-01' -Method PATCH -Body @{
        properties = @{ httpsOnly = $true }
    }
    Grant-PortalHostRole $subscription $principal "/subscriptions/$subscription" 'Reader'
    Grant-PortalHostRole $subscription $principal $Plan.AppResourceId 'Website Contributor'
    $registryMode = Invoke-PortalAzJson -Arguments @('acr', 'show', '--subscription', $subscription, '--name', $inputs.RegistryName, '--query', 'roleAssignmentMode') -Operation 'registry role-assignment mode'
    if ($registryMode -notin @('LegacyRegistryPermissions', 'AbacRepositoryPermissions')) { throw 'Registry permission mode is unknown; no pull role will be guessed.' }
    $pullRole = if ($registryMode -eq 'AbacRepositoryPermissions') { 'Container Registry Repository Reader' } else { 'AcrPull' }
    Grant-PortalHostRole $subscription $principal $Plan.RegistryId $pullRole
    $authPath = "$($Plan.AppResourceId)/config/authsettingsV2?api-version=2024-11-01"
    $auth = Invoke-PortalArm -Subscription $subscription -Path $authPath
    $properties = $auth.properties
    if (-not $properties) { $properties = @{} }
    foreach ($section in @('platform', 'httpSettings', 'globalValidation')) {
        if (-not $properties[$section]) { $properties[$section] = @{} }
    }
    $properties.platform['enabled'] = $true
    $properties.httpSettings['requireHttps'] = $true
    $hasProvider = [bool]$properties.identityProviders.azureActiveDirectory.registration.clientId
    if (-not $hasProvider -and $properties.identityProviders.customOpenIdConnectProviders.Count -gt 0) {
        throw 'Existing custom authentication is not replaced. Configure Entra explicitly through the hosting portal.'
    }
    $properties.globalValidation['requireAuthentication'] = $true
    $properties.globalValidation['excludedPaths'] = @()
    $properties.globalValidation['unauthenticatedClientAction'] = if ($hasProvider) { 'RedirectToLoginPage' } else { 'Return401' }
    $properties.globalValidation['redirectToProvider'] = 'azureActiveDirectory'
    $null = Invoke-PortalArm -Subscription $subscription -Path $authPath -Method PUT -Body @{ properties = $properties } -ETag $auth.etag
    $currentSettings = Invoke-PortalArm -Subscription $subscription -Path "$settingsPath/list?api-version=2024-11-01" -Method POST
    if ((ConvertTo-Json -InputObject $currentSettings.properties -Depth 40 -Compress) -cne
        (ConvertTo-Json -InputObject $settings.properties -Depth 40 -Compress)) {
        throw 'App settings changed during prerequisite provisioning; inspect changes and rerun. No settings were overwritten.'
    }
    $values['GALLERY_ADMIN_OIDS'] = $Plan.AdministratorIds
    $values['GALLERY_EASY_AUTH'] = 'true'
    $values['GALLERY_HTTPS'] = 'true'
    $values['GALLERY_INSTALLER_ENABLED'] = 'false'
    $values['GALLERY_MANAGED_IDENTITY'] = $identityMode
    if ($identityMode -eq 'user') { $values['AZURE_CLIENT_ID'] = $hostIdentity.ClientId }
    $values['WEBSITES_ENABLE_APP_SERVICE_STORAGE'] = 'true'
    $values['WEBSITES_PORT'] = '8000'
    $null = Invoke-PortalArm -Subscription $subscription -Path "$settingsPath`?api-version=2024-11-01" -Method PUT -Body @{ properties = $values } -ETag $settings.etag
    Enable-PortalAlwaysOn $Plan
    $site = Invoke-PortalPrerequisiteResource $subscription $Plan.AppResourceId '2024-11-01'
    if (-not $snapshot.Site -and $site.properties.state -ne 'Stopped') {
        throw 'The new host has not reached Stopped state. Inspect Azure before continuing with deployment.'
    }
    if ($site.properties.httpsOnly -ne $true) {
        throw 'Hosting HTTPS settings have not converged. Inspect Azure and rerun before deploying.'
    }
    return [pscustomobject]@{
        Status = 'HostingPrepared'; AppResourceId = $Plan.AppResourceId
        Origin = "https://$($site.properties.defaultHostName)"; Registry = $inputs.RegistryName
        LoginServer = $registry.properties.loginServer; EntraConfigured = $hasProvider; RuntimeVerified = $false
    }
}

function Connect-PortalPrerequisiteEntra {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param([object]$Plan, [string]$Origin, [string]$ClientId, [switch]$NonInteractive)
    $client = ConvertTo-PortalGuid $ClientId
    Test-PortalEntraRegistration -Plan ([pscustomobject]@{ TenantId = $Plan.TenantId; EntraClientId = $client; Origin = $Origin })
    $path = "$($Plan.AppResourceId)/config/authsettingsV2?api-version=2024-11-01"
    $current = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path $path
    $provider = $current.properties.identityProviders.azureActiveDirectory
    if ($provider.registration.clientId -and $provider.registration.clientId -ne $client) {
        throw 'Another Entra application is already configured. The prerequisite workflow will not replace it.'
    }
    if ($provider.registration.clientId -eq $client) {
        Write-PortalStatus OK 'Existing Entra connection retained; final deployment validates its issuer and access policy.'
        return
    }
    if ($current.properties.identityProviders.customOpenIdConnectProviders.Count -gt 0) {
        throw 'Configure existing custom providers through the hosting portal; they will not be replaced.'
    }
    if (-not $PSCmdlet.ShouldProcess($Plan.AppResourceId, 'Connect the selected organization Entra registration and store its credential only in protected App Service settings')) { return }
    $settingsPath = "$($Plan.AppResourceId)/config/appsettings"
    $settings = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path "$settingsPath/list?api-version=2024-11-01" -Method POST
    $settingName = 'PORTAL_ENTRA_CLIENT_SECRET'
    if (-not $settings.properties[$settingName]) {
        if ($NonInteractive) { throw 'Preconfigure PORTAL_ENTRA_CLIENT_SECRET (or a resolved vault reference) through the host secret store before unattended Entra connection.' }
        Write-PortalSection 'ENTRA CREDENTIAL | Existing app registration'
        Write-PortalStatus INFO 'A client secret is the application password that App Service Authentication uses during Entra sign-in. It is not your personal account password.'
        Write-PortalStatus INFO 'You selected an existing registration, but this App Service does not have its credential configured. Existing mode does not automatically create or reset secrets.'
        Write-PortalStatus ACTION "In Microsoft Entra ID > App registrations, open Application (client) ID: $client in tenant $($Plan.TenantId)."
        Write-PortalStatus ACTION 'Open Certificates & secrets > Client secrets. You need the text from the Value field, not the Secret ID or Application (client) ID.'
        Write-PortalStatus INFO 'Entra shows a secret Value only when it is created. Use a valid value from your approved secret store. If it is unavailable, an authorized administrator can add a new client secret with an organization-approved expiry and record its Value immediately. Keep existing secrets that other apps may use.'
        Write-PortalStatus INFO 'Enter the value only in the next terminal prompt, never in chat. The terminal masks the characters you type or paste.'
        Write-PortalStatus INFO 'The script saves the credential in the protected App Service setting PORTAL_ENTRA_CLIENT_SECRET. It does not print the value or write it to local files, including your saved Prepare configuration.'
        Write-PortalStatus INFO 'Press Ctrl+C to pause if you need to arrange the credential. Hosting already prepared remains in place.'
        $secure = Read-Host 'Paste the client secret Value, then press Enter' -AsSecureString
        try {
            $value = [Net.NetworkCredential]::new('', $secure).Password
            if (-not $value -or $value.Length -gt 4096) { throw 'A valid client secret value is required.' }
            $settings.properties[$settingName] = $value
            $null = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path "$settingsPath`?api-version=2024-11-01" -Method PUT -Body @{ properties = $settings.properties } -ETag $settings.etag
        } finally { $value = $null; if ($secure) { $secure.Dispose() }; $settings = $null }
    }
    $properties = $current.properties
    if (-not $properties) { $properties = @{} }
    foreach ($section in @('platform', 'httpSettings', 'globalValidation')) {
        if (-not $properties[$section]) { $properties[$section] = @{} }
    }
    $properties.platform['enabled'] = $true
    $properties.httpSettings['requireHttps'] = $true
    if (-not $properties.identityProviders) { $properties['identityProviders'] = @{} }
    $properties.identityProviders['azureActiveDirectory'] = @{
        enabled = $true; registration = @{ clientId = $client; clientSecretSettingName = $settingName
            openIdIssuer = "https://login.microsoftonline.com/$($Plan.TenantId)/v2.0" }
        validation = @{ allowedAudiences = @($client) }
    }
    $properties.globalValidation['requireAuthentication'] = $true
    $properties.globalValidation['unauthenticatedClientAction'] = 'RedirectToLoginPage'
    $properties.globalValidation['redirectToProvider'] = 'azureActiveDirectory'
    $properties.globalValidation['excludedPaths'] = @()
    $null = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path $path -Method PUT -Body @{ properties = $properties } -ETag $current.etag
    Write-PortalStatus OK 'Entra hosting configuration saved. This has not signed anyone in or completed browser setup.'
}

function Test-PortalPrerequisiteImageReference {
    param([string]$ImageReference, [switch]$ForBuild)
    $repositoryPattern = '[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*'
    $tagPattern = ':[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}'
    $suffixPattern = if ($ForBuild) { $tagPattern } else { "(?:$tagPattern|@sha256:[a-f0-9]{64})" }
    return $ImageReference -cmatch "\A$repositoryPattern$suffixPattern\z"
}

function Publish-PortalPrerequisiteImage {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param([object]$Plan, [string]$LoginServer, [string]$ImageReference, [string]$PipConfigPath)
    if (-not (Test-PortalPrerequisiteImageReference $ImageReference -ForBuild) -or
        $LoginServer -notmatch '^[a-z0-9][a-z0-9.-]*\.azurecr\.io$') { throw 'Use repository:tag for image build, for example deployment-portal:latest (not latest alone).' }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is unavailable here. Build/push from the connected workstation, then use Deploy-Portal.ps1.' }
    if ($PipConfigPath -and (-not (Test-Path -LiteralPath $PipConfigPath -PathType Leaf) -or $PipConfigPath.Contains(','))) {
        throw 'The optional approved pip config must be an existing file with no comma in its path.'
    }
    $image = "$LoginServer/$ImageReference"
    if (-not $PSCmdlet.ShouldProcess($image, 'Build linux/amd64 organization image and push to the selected ACR using the operator identity')) {
        return [pscustomobject]@{ Status = 'Cancelled'; Image = $image }
    }
    Write-PortalStatus INFO 'Signing the operator into the selected container registry.'
    $null = & az acr login --subscription $Plan.SubscriptionId --name $Plan.Inputs.RegistryName --only-show-errors --output none 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Operator registry login failed; check service reachability and push permissions under organization policy.' }
    $arguments = @('build', '--platform', 'linux/amd64', '--tag', $image)
    if ($PipConfigPath) { $arguments += @('--secret', "id=pip_config,src=$([IO.Path]::GetFullPath($PipConfigPath))") }
    $arguments += (Split-Path $PSScriptRoot -Parent)
    Write-PortalSection 'DOCKER BUILD | Native Docker output below'
    & docker @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Image build failed; no push submitted.' }
    Write-PortalSection 'DOCKER PUSH | Native Docker output below'
    & docker push $image | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Image push failed. Check operator push roles and service reachability under organization policy.' }
    Write-PortalSection 'IMAGE PUBLICATION | Result'
    Write-PortalStatus OK "Image built and pushed: $image"
    Write-PortalStatus INFO 'The App Service image has not been changed yet. Deployment requires its own confirmation.'
    return [pscustomobject]@{ Status = 'ImagePublished'; Image = $image }
}

function Initialize-PortalHosting {
    <#
    .SYNOPSIS
    Interactively prepares authenticated hosting and guides Entra and image prerequisites.
    .EXAMPLE
    Initialize-PortalHosting -WhatIf
    #>
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param(
        [string]$SubscriptionId, [string]$ResourceGroupName, [string]$Location,
        [string]$AppName, [string]$PlanName, [string]$RegistryName,
        [string]$PlanResourceId, [string]$RegistryResourceId,
        [string]$ManagedIdentityResourceId, [string]$NewManagedIdentityName,
        [ValidateSet('B1','B2','B3','S1','P1v3')][string]$UpgradePlanSku,
        [switch]$RepairEntra,
        [ValidateSet('F1','B1','B2','B3','S1','P1v3')][string]$PlanSku = 'B1',
        [string[]]$AdminObjectIds, [string]$EntraClientId,
        [ValidateSet('Create','Existing','Later')][string]$EntraMode,
        [string]$EntraDisplayName, [ValidateRange(7,180)][int]$CredentialDays = 90,
        [string]$ConfigPath, [switch]$Fresh,
        [switch]$NonInteractive
    )
    $ErrorActionPreference = 'Stop'
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Install Azure CLI, or use PowerShell in Azure Cloud Shell.' }
    Write-PortalSection 'DEPLOYMENT PORTAL | Hosting preparation'
    Write-PortalStatus INFO 'Organization-owned hosting, Entra authentication and scoped host roles.'
    Write-PortalStatus INFO 'Networking stays organization-managed. No VPN, subnet, DNS or private-endpoint setup is required by this wizard.'
    Write-PortalStatus INFO 'Legend: [PLAN] script work after approval; [OK] passed; [INFO] context; [WARN] limitation; [BLOCKED] needs attention; [ACTION] your manual step.'
    if (-not $ConfigPath) { $ConfigPath = Join-Path $PSScriptRoot '../.portal/prepare.json' }
    $ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
    $savedConfiguration = $null
    $explicitSelections = @(Get-PortalPrepareSelectionName | Where-Object { $PSBoundParameters.ContainsKey($_) })
    if (-not $Fresh -and $explicitSelections.Count -eq 0) {
        if (Test-Path -LiteralPath $ConfigPath) {
            $savedConfiguration = Import-PortalPrepareConfiguration $ConfigPath
            foreach ($name in Get-PortalPrepareSelectionName) {
                if ($name -eq 'UpgradePlanSku' -and -not $savedConfiguration.Selections[$name]) { continue }
                Set-Variable -Name $name -Value $savedConfiguration.Selections[$name] -Scope Local -WhatIf:$false -Confirm:$false
            }
            Write-PortalStatus INFO "Loaded Prepare selections: $ConfigPath"
            Write-PortalStatus INFO 'Readiness is checked again. Saved selections do not approve repairs, resource changes, secret entry, image operations or consent. Use -Fresh to choose again.'
        } elseif ($PSBoundParameters.ContainsKey('ConfigPath') -and -not $WhatIfPreference) {
            throw 'Prepare configuration was not found. Run Prepare -WhatIf with this -ConfigPath first, or use -Fresh to select resources.'
        }
    } elseif (Test-Path -LiteralPath $ConfigPath) {
        Write-PortalStatus INFO 'Saved Prepare selections are ignored because -Fresh or explicit selections were supplied. No saved and explicit resource choices are combined.'
    }
    Write-PortalSection 'DISCOVERY | Select or reuse organization resources'
    if (-not $NonInteractive -and -not $savedConfiguration) {
        $discovered = Get-PortalDiscoveredInputs @{
            SubscriptionId=$SubscriptionId; ResourceGroupName=$ResourceGroupName; Location=$Location
            AppName=$AppName; PlanName=$PlanName; RegistryName=$RegistryName; PlanSku=$PlanSku
            PlanResourceId=$PlanResourceId; RegistryResourceId=$RegistryResourceId
            ManagedIdentityResourceId=$ManagedIdentityResourceId; NewManagedIdentityName=$NewManagedIdentityName
            UpgradePlanSku=$UpgradePlanSku
            PlanSkuProvided=$PSBoundParameters.ContainsKey('PlanSku'); AdminObjectIds=$AdminObjectIds
            EntraClientId=$EntraClientId; EntraMode=$EntraMode; EntraDisplayName=$EntraDisplayName
        }
        $SubscriptionId = $discovered.Inputs.SubscriptionId
        $ResourceGroupName = $discovered.Inputs.ResourceGroupName
        $Location = $discovered.Inputs.Location
        $AppName = $discovered.Inputs.AppName
        $PlanName = $discovered.Inputs.PlanName
        $RegistryName = $discovered.Inputs.RegistryName
        $PlanResourceId = $discovered.Inputs.PlanResourceId
        $RegistryResourceId = $discovered.Inputs.RegistryResourceId
        $ManagedIdentityResourceId = $discovered.Inputs.ManagedIdentityResourceId
        $NewManagedIdentityName = $discovered.Inputs.NewManagedIdentityName
        if ($discovered.Inputs.UpgradePlanSku) { $UpgradePlanSku = $discovered.Inputs.UpgradePlanSku }
        $PlanSku = $discovered.Inputs.PlanSku
        $AdminObjectIds = $discovered.Inputs.AdminObjectIds
        $EntraMode = $discovered.EntraMode
        $EntraClientId = $discovered.EntraClientId
        $EntraDisplayName = $discovered.EntraDisplayName
    }
    $inputs = @{
        SubscriptionId = ConvertTo-PortalGuid (Read-PortalDeploymentInput $SubscriptionId 'Subscription ID (sign in to Azure CLI first)' -NonInteractive:$NonInteractive)
        ResourceGroupName = Read-PortalDeploymentInput $ResourceGroupName 'Hosting group to create/reuse (CAF: rg-portal-test-region-001)' -NonInteractive:$NonInteractive
        Location = Read-PortalDeploymentInput $Location 'Azure hosting region (must match existing resources when reused)' -NonInteractive:$NonInteractive
        AppName = Read-PortalDeploymentInput $AppName 'Web app to create/reuse (app-portal-test-region-001)' -NonInteractive:$NonInteractive
        PlanName = Read-PortalDeploymentInput $PlanName 'App Service plan to create/reuse (asp-portal-test-region-001)' -NonInteractive:$NonInteractive
        RegistryName = Read-PortalDeploymentInput $RegistryName 'Organization registry to create/reuse (globally unique lowercase alphanumeric cr... name)' -NonInteractive:$NonInteractive
        PlanSku = $PlanSku
        PlanResourceId = $PlanResourceId
        RegistryResourceId = $RegistryResourceId
        ManagedIdentityResourceId = $ManagedIdentityResourceId
        NewManagedIdentityName = $NewManagedIdentityName
        UpgradePlanSku = $UpgradePlanSku
    }
    if (-not $AdminObjectIds) { $AdminObjectIds = (Read-PortalDeploymentInput '' 'Initial administrator USER object IDs, comma-separated' -NonInteractive:$NonInteractive).Split(',') }
    $inputs.AdminObjectIds = @($AdminObjectIds | ForEach-Object { ConvertTo-PortalGuid $_.Trim() })
    foreach ($key in @('ResourceGroupName','AppName','PlanName','RegistryName','Location')) {
        if ($inputs[$key] -notmatch '^[A-Za-z0-9][A-Za-z0-9-]*$') { throw 'Use literal resource names, without URI/query/control characters.' }
    }
    if (-not $EntraMode) {
        if ($EntraClientId) { $EntraMode = 'Existing' }
        elseif ($NonInteractive) { throw 'Missing required input: EntraMode (Create, Existing or Later).' }
        else { $EntraMode = Read-PortalFoundationValue 'Entra registration: Create, Existing or Later' 'Create' }
    }
    if ($EntraMode -notin @('Create','Existing','Later')) { throw 'Choose Create, Existing or Later for Entra.' }
    if ($EntraMode -eq 'Create') {
        $EntraDisplayName = Read-PortalDeploymentInput $EntraDisplayName 'New organization Entra app registration display name' -NonInteractive:$NonInteractive
        if (-not $PSBoundParameters.ContainsKey('CredentialDays') -and -not $NonInteractive -and -not $savedConfiguration) {
            $CredentialDays = [int](Read-PortalFoundationValue 'Generated credential lifetime in days (7-180)' '90')
        }
        if ($CredentialDays -lt 7 -or $CredentialDays -gt 180) { throw 'Credential lifetime must be 7-180 days.' }
    } elseif ($EntraMode -eq 'Existing') {
        $EntraClientId = ConvertTo-PortalGuid (Read-PortalDeploymentInput $EntraClientId 'Existing organization Entra client ID' -NonInteractive:$NonInteractive)
    }
    $snapshot = Get-PortalPrerequisiteSnapshot $inputs
    $plan = Get-PortalPrerequisitePlan $inputs $snapshot
    if ($savedConfiguration -and $plan.TenantId -ne $savedConfiguration.TenantId) {
        throw 'The saved Prepare tenant no longer matches the selected subscription. No writes were sent. Use -Fresh to review the target again.'
    }
    Write-PortalSection 'HOSTING CHECKS | Before any changes'
    $hostingStatus = switch ($plan.HostingReadiness.Status) { 'Ready' { 'OK' }; 'Warning' { 'WARN' }; 'Blocked' { 'BLOCKED' }; default { 'INFO' } }
    Write-PortalStatus $hostingStatus $plan.HostingReadiness.Detail
    $entraReadiness = Get-PortalEntraReadiness -Plan $plan -Snapshot $snapshot -ClientId $EntraClientId -Mode $EntraMode
    Write-PortalEntraReadiness $entraReadiness
    if ($entraReadiness.Status -eq 'RequiresRepair' -and -not $RepairEntra -and -not $NonInteractive) {
        $repairChoice = Read-PortalChoice 'Plan the listed Entra repairs?' @(
            @{ Value='Yes'; Label='Let the script perform these repairs after separate confirmation' },
            @{ Value='No'; Label='Do not let the script repair; leave settings unchanged and stop before deployment' }) 'No'
        $RepairEntra = $repairChoice -eq 'Yes'
    }
    Write-PortalSection 'PLAN | Review resources and changes'
    Write-PortalStatus INFO "Entra mode: $EntraMode; client ID: $EntraClientId"
    if ($EntraDisplayName) { Write-PortalStatus INFO "Registration name: $EntraDisplayName" }
    Write-PortalStatus INFO "Tenant: $($plan.TenantId)"
    foreach ($step in $plan.Steps) {
        Write-Host ''
        Write-PortalStatus PLAN $step.Action
        Write-Host "  $($step.Resource)" -ForegroundColor Gray
    }
    Write-PortalStatus WARN 'Review budget and backups: paid App Service tiers and ACR are billable. No networking changes or workload Contributor/Owner grants are included.'
    if ($NonInteractive -and -not $WhatIfPreference -and (-not $PSBoundParameters.ContainsKey('Confirm') -or $PSBoundParameters.Confirm)) {
        throw 'Unattended apply requires all inputs and explicit -Confirm:$false after reviewing -WhatIf.'
    }
    $confirmation = @{ WhatIf = [bool]$WhatIfPreference }
    if ($PSBoundParameters.ContainsKey('Confirm')) { $confirmation.Confirm = $PSBoundParameters.Confirm }
    $blocking = $plan.HostingReadiness.Status -eq 'Blocked' -or $entraReadiness.Status -eq 'Blocked' -or ($entraReadiness.Status -eq 'RequiresRepair' -and -not $RepairEntra)
    $blockingFindings = @(
        if ($plan.HostingReadiness.Status -eq 'Blocked') {
            [pscustomobject]@{ Check=$plan.HostingReadiness.Check; Detail=$plan.HostingReadiness.Detail }
        }
        if ($entraReadiness.Status -eq 'Blocked') {
            $entraReadiness.Checks | Where-Object { $_.Status -eq 'Blocked' } | Select-Object Check,Detail
        }
        if ($entraReadiness.Status -eq 'RequiresRepair' -and -not $RepairEntra) {
            [pscustomobject]@{ Check='Entra repair approval'; Detail='Required Entra repairs were not approved. Review the findings and choose Yes at "Plan the listed Entra repairs?", or correct the registration yourself before retrying.' }
            $entraReadiness.Checks | Where-Object { $_.Status -eq 'Repairable' } | Select-Object Check,Detail
        }
    )
    if ($WhatIfPreference) {
        Write-PortalSection 'PREVIEW RESULT | No Azure changes applied'
        Write-PortalStatus $(if ($blocking) { 'BLOCKED' } else { 'OK' }) "Ready for reviewed preparation: $(-not $blocking). This is not sign-in or runtime verification."
        Write-PortalStatus INFO 'No resources, credentials, app settings or directory policies were changed.'
        $savedPath = $null
        if (-not $blocking -and $EntraMode -ne 'Later') {
            $selections = @{} + $inputs
            $selections.EntraMode = $EntraMode
            $selections.EntraClientId = $EntraClientId
            $selections.EntraDisplayName = $EntraDisplayName
            $selections.CredentialDays = $CredentialDays
            Export-PortalPrepareConfiguration -Path $ConfigPath -TenantId $plan.TenantId -Selections $selections
            $savedPath = $ConfigPath
            Write-PortalStatus OK "Saved non-secret Prepare selections locally: $ConfigPath"
            Write-PortalStatus INFO 'Run Prepare again to reuse these selections; keep -ConfigPath if you used a custom path. Readiness and approvals are still required. Keep this organization-specific file out of source control.'
        } else {
            Write-PortalStatus INFO 'No Prepare configuration saved because readiness is blocked or Entra was deferred. Any earlier configuration is unchanged; use -Fresh to avoid reusing an older selection.'
        }
        return [pscustomobject]@{ Status='PreviewOnly'; AppResourceId=$plan.AppResourceId
            ConfigurationPath=$savedPath; ConfigurationSaved=[bool]$savedPath
            ReadyForApply=(-not $blocking); HostingReadiness=$plan.HostingReadiness.Status; EntraReadiness=$entraReadiness.Status
            EntraChecks=@($entraReadiness.Checks | ForEach-Object { [pscustomobject]@{ Check=$_.Check; Status=$_.Status; Detail=$_.Detail } })
            BlockingFindings=$blockingFindings
            EntraRepairPlanned=[bool]$RepairEntra; RuntimeVerified=$false; SignInVerified=$false }
    }
    if ($blocking) {
        $details = ($blockingFindings | ForEach-Object { "$($_.Check): $($_.Detail)" }) -join [Environment]::NewLine
        Write-PortalSection 'BLOCKED | No preparation writes sent'
        $blockingFindings | ForEach-Object { Write-PortalStatus BLOCKED "$($_.Check): $($_.Detail)" }
        throw "Preparation blocked (hosting: $($plan.HostingReadiness.Status); Entra: $($entraReadiness.Status)).`n$details`nNo preparation writes were sent."
    }
    if ($entraReadiness.Status -eq 'RequiresRepair') {
        if (-not (Repair-PortalEntraReadiness -Plan $plan -Snapshot $snapshot -Readiness $entraReadiness @confirmation)) {
            return [pscustomobject]@{ Status='Cancelled'; AppResourceId=$plan.AppResourceId }
        }
    }
    Write-PortalSection 'APPLY HOSTING | Confirmation required'
    Write-PortalStatus INFO 'Yes to All applies only within the current confirmation scope. Image publication and deployment have separate approvals.'
    $result = Set-PortalPrerequisites -Plan $plan @confirmation
    if ($result.Status -in @('PreviewOnly','Cancelled')) {
        Write-PortalSection 'PREPARATION RESULT | Stopped'
        Write-PortalStatus INFO 'Hosting preparation did not complete. No image was built or deployed.'
        return $result
    }
    Write-PortalStatus OK 'Hosting prepared. No new image has been deployed by this preparation step.'
    Write-PortalStatus INFO "Portal: $($result.Origin)"
    Write-PortalStatus INFO "Registry: $($result.LoginServer)"
    Write-PortalStatus INFO "Entra Web callback: $($result.Origin)/.auth/login/aad/callback"
    if ($EntraMode -eq 'Create') {
        $registration = New-PortalCustomerRegistration -Plan $plan -Origin $result.Origin -DisplayName $EntraDisplayName -CredentialDays $CredentialDays @confirmation
        if ($registration.Status -eq 'PreviewOnly') { return $result }
        $EntraClientId = $registration.ClientId
    }
    if ($EntraMode -eq 'Existing') {
        Write-PortalSection 'ENTRA | Validate the prepared host before connection'
        $preparedSnapshot = Get-PortalPrerequisiteSnapshot $inputs
        $preparedEntra = Get-PortalEntraReadiness -Plan $plan -Snapshot $preparedSnapshot -ClientId $EntraClientId -Mode Existing
        Write-PortalEntraReadiness $preparedEntra
        if ($preparedEntra.Status -eq 'RequiresRepair') {
            Write-PortalStatus INFO 'Hosting is retained. Any Entra repair uses the actual host callback and requires separate approval; existing redirect URLs and credentials are preserved.'
            if (-not $RepairEntra -and -not $NonInteractive) {
                $repairChoice = Read-PortalChoice 'Plan the listed Entra repairs for the prepared host?' @(
                    @{ Value='Yes'; Label='Let the script perform these repairs after separate confirmation' },
                    @{ Value='No'; Label='Stop with hosting retained; leave the registration unchanged' }) 'No'
                $RepairEntra = $repairChoice -eq 'Yes'
            }
            if (-not $RepairEntra) {
                throw 'The prepared host needs Entra repairs that were not approved. Hosting is retained; no image was deployed. Rerun Prepare using the same resources and approve the listed repairs, or correct the registration manually.'
            }
            if (-not (Repair-PortalEntraReadiness -Plan $plan -Snapshot $preparedSnapshot -Readiness $preparedEntra @confirmation)) {
                Write-PortalStatus INFO 'Entra repair declined. Hosting is retained; provider connection and image deployment were not attempted.'
                return $result
            }
            $preparedEntra = Get-PortalEntraReadiness -Plan $plan -Snapshot $preparedSnapshot -ClientId $EntraClientId -Mode Existing
        }
        if ($preparedEntra.Status -ne 'Ready') {
            throw 'Prepared-host Entra readiness is not verified. Inspect the listed findings and rerun with the same resources; hosting is retained and no provider connection or image deployment was attempted.'
        }
        Connect-PortalPrerequisiteEntra -Plan $plan -Origin $preparedEntra.Origin -ClientId $EntraClientId -NonInteractive:$NonInteractive @confirmation
    }
    if ($EntraMode -ne 'Later') {
        $verifiedSnapshot = Get-PortalPrerequisiteSnapshot $inputs
        $verifiedEntra = Get-PortalEntraReadiness $plan $verifiedSnapshot $EntraClientId Existing
        Write-PortalEntraReadiness $verifiedEntra
        if ($verifiedEntra.Status -ne 'Ready') { throw 'Post-configuration Entra readiness is not verified. Inspect the findings before image deployment.' }
    }
    Write-PortalStatus INFO 'Only organization-authorized users should be assigned to the Entra enterprise application. Organization networking remains unchanged.'
    if ($NonInteractive -or $EntraMode -eq 'Later') {
        Write-PortalSection 'PREPARATION RESULT | Hosting prepared'
        Write-PortalStatus ACTION 'Deploy the reviewed image separately. Administrator consent and browser sign-in remain to be verified.'
        if ($EntraMode -eq 'Later') { Write-PortalStatus ACTION 'Complete Entra configuration before deploying the image.' }
        return $result
    }
    if ($verifiedEntra.Checks.Count -gt 0 -and -not @($verifiedEntra.Checks | Where-Object { $_.Status -ne 'Ready' }).Count) {
        $consentNext = Read-PortalChoice 'Admin consent checkpoint (no consent is granted here)' @(
            @{ Value='Continue'; Label='Continue to image selection; administrator consent and browser verification remain separate' },
            @{ Value='Stop'; Label='Stop with hosting prepared to arrange administrator consent' }) 'Continue'
        if ($consentNext -eq 'Stop') { Write-PortalStatus ACTION 'Hosting is prepared. Arrange administrator consent, then deploy the image when ready.'; return $result }
    }
    Write-PortalSection 'IMAGE | Choose the next operation'
    $next = Read-Host 'Next: B = build, push and deploy with local Docker, D = deploy a published image, Enter = stop here (hosting prepared; deploy later)'
    if ($next -notin @('B','D')) {
        Write-PortalStatus INFO 'Stopped with hosting prepared. No image was built, pushed or deployed in this run.'
        return $result
    }
    $imagePrompt = if ($next -eq 'B') {
        'Image repository:tag for build (example: deployment-portal:latest; no registry hostname; Q = stop)'
    } else {
        'Image repository:tag or repository@sha256:digest (example: deployment-portal:latest; no registry hostname; Q = stop)'
    }
    while ($true) {
        $image = ([string](Read-Host $imagePrompt)).Trim()
        if ($image -eq 'Q') { Write-PortalStatus INFO 'Image selection cancelled. Prepared hosting is retained; no image operation was started.'; return $result }
        if (Test-PortalPrerequisiteImageReference $image -ForBuild:($next -eq 'B')) { break }
        if ($next -eq 'B') {
            Write-Warning 'Enter a repository AND tag, for example deployment-portal:latest, not latest alone. Digests cannot be used for a new build. Q stops without changing the prepared hosting.'
        } else {
            Write-Warning 'Enter repository:tag (for example deployment-portal:latest) or repository@sha256: followed by a 64-character hexadecimal digest. A tag alone is incomplete. Q stops without deploying.'
        }
    }
    Write-PortalStatus INFO "Selected image: $($result.LoginServer)/$image"
    if ($next -eq 'B') {
        $pipConfig = Read-Host 'Approved pip configuration path for this build (Enter = default feed)'
        $publication = Publish-PortalPrerequisiteImage -Plan $plan -LoginServer $result.LoginServer -ImageReference $image -PipConfigPath $pipConfig @confirmation
        if ($publication.Status -ne 'ImagePublished') { Write-PortalStatus INFO 'Image publication cancelled. Deployment was not started.'; return $publication }
    }
    Invoke-PortalDeployment -SubscriptionId $plan.SubscriptionId -ResourceGroupName $inputs.ResourceGroupName -AppName $inputs.AppName -RegistryName $inputs.RegistryName -Image $image -AdminObjectIds $inputs.AdminObjectIds @confirmation
}

if ($MyInvocation.InvocationName -ne '.') {
    Initialize-PortalHosting @initializerParameters
}
