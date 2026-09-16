#Requires -Version 7.0

<#
.SYNOPSIS
Interactively prepares organization hosting or deploys an image to authenticated App Service.
.DESCRIPTION
Uses the signed-in Azure CLI account and offers a Prepare/Deploy menu. Prepare
invokes Initialize-Portal.ps1 for confirmed hosting, Entra and host roles.
Deploy validates existing hosting and Entra before applying a published image.
Use -WhatIf for read-only discovery. Deploy requires registry data-plane access.
Prepare and Deploy print a tenant-specific administrator-consent page link and
a separate portal sign-in link. The consent return URI uses the selected host's
exact Web callback, not a default callback from a reused registration.
The v2 consent endpoint explicitly requests openid profile email, so basic sign-in
does not depend on a configured static API-permissions list. No additional API
permissions are requested by this link; review those separately when needed.
Approval remains a manual browser operation;
the script does not grant or verify consent or bypass employee assignments.
.PARAMETER SubscriptionId
Hosting subscription ID. Prompted when omitted.
.PARAMETER ResourceGroupName
Existing App Service resource group. Prompted when omitted.
.PARAMETER AppName
Existing Linux App Service name. Prompted when omitted.
.PARAMETER RegistryName
Existing Azure Container Registry name in the selected subscription.
.PARAMETER Image
Published repository:tag or repository@sha256:digest, without registry hostname.
.PARAMETER AdminObjectIds
Initial portal administrator user object IDs in the hosting tenant.
.PARAMETER NonInteractive
Require all inputs as parameters. Unattended apply additionally requires -Confirm:$false.
.PARAMETER Mode
Prepare hosting prerequisites or Deploy a published image. Interactive runs offer a menu when omitted.
.PARAMETER ConfigPath
Prepare-only JSON selections file; defaults to .portal/prepare.json beside the tools folder.
A successful Prepare WhatIf saves non-secret selections for the next Prepare run.
.PARAMETER Fresh
Prepare-only switch to ignore saved selections and choose resources again.
.EXAMPLE
./tools/Deploy-Portal.ps1 -WhatIf
.NOTES
Run in Azure Cloud Shell PowerShell or PowerShell 7 with Azure CLI.
Docker is needed only for Prepare's optional image build. Prepare can request
an existing Entra secret with hidden input; Deploy never requests secrets.
Organization network settings are not provisioned, changed or required by the scripts.
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
param(
    [string]$SubscriptionId,
    [string]$ResourceGroupName,
    [string]$AppName,
    [string]$RegistryName,
    [string]$Image,
    [string[]]$AdminObjectIds,
    [switch]$NonInteractive,
    [string]$ConfigPath,
    [switch]$Fresh,
    [ValidateSet('Prepare','Deploy')][string]$Mode
)

function Write-PortalSection {
    param([string]$Title)
    Write-Host ''
    Write-Host ('-' * 64) -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ('-' * 64) -ForegroundColor Cyan
}

function Write-PortalStatus {
    param([ValidateSet('OK','INFO','WARN','BLOCKED','ACTION','PLAN')][string]$Status, [string]$Message)
    if ($Status -eq 'WARN') {
        Write-Warning "[WARN] $Message"
        return
    }
    $color = switch ($Status) {
        'OK' { 'Green' }
        'BLOCKED' { 'Red' }
        'ACTION' { 'Magenta' }
        'PLAN' { 'Cyan' }
        default { 'Gray' }
    }
    Write-Host "[$Status] $Message" -ForegroundColor $color
}

function Write-PortalConsentAction {
    param([string]$TenantId, [string]$ClientId, [string]$Origin)
    $callback = $null
    if ($Origin) {
        $originUri = $null
        if (-not [uri]::TryCreate($Origin, [UriKind]::Absolute, [ref]$originUri) -or
            $originUri.Scheme -ne 'https' -or -not $originUri.Host -or $originUri.UserInfo -or
            $originUri.AbsolutePath -ne '/' -or $originUri.Query -or $originUri.Fragment) {
            throw 'The consent return host must be an HTTPS origin without credentials, a path, query or fragment.'
        }
        $Origin = $originUri.GetLeftPart([UriPartial]::Authority)
        $callback = "$Origin/.auth/login/aad/callback"
    }
    $consentUrl = if ($TenantId -and $ClientId) {
        $tenant = ConvertTo-PortalGuid $TenantId
        $client = ConvertTo-PortalGuid $ClientId
        if ($callback) {
            "https://login.microsoftonline.com/$tenant/v2.0/adminconsent?client_id=$client&scope=openid%20profile%20email&redirect_uri=$([uri]::EscapeDataString($callback))"
        }
    }
    Write-PortalSection 'ADMIN CONSENT | Manual action before employee sign-in'
    if ($TenantId) { Write-PortalStatus INFO "Tenant: $TenantId" }
    if ($ClientId) { Write-PortalStatus INFO "Application (client) ID: $ClientId" }
    Write-PortalStatus WARN 'Admin consent is NOT VERIFIED. Assigning users does not grant consent; assignment-required apps need tenant-wide admin consent even for basic sign-in.'
    Write-PortalStatus WARN 'Without administrator consent, employees can see Need admin approval even when deployment succeeds. Resolve consent before treating the portal as ready for users.'
    Write-PortalStatus ACTION '1. Ask an authorized Entra administrator to review the actual app sign-in permissions. Azure subscription Owner and portal administrator are not consent roles.'
    if ($consentUrl) {
        Write-PortalStatus ACTION "Administrator consent page: $consentUrl"
        Write-PortalStatus INFO "Consent return callback for the selected host: $callback"
        Write-PortalStatus INFO 'This exact Web redirect must exist on the registration. The link explicitly selects this host, even when the registration has callbacks for other apps.'
        Write-PortalStatus INFO 'Open this link in your browser as the authorized tenant administrator and review before accepting. It requests only the basic sign-in scopes: openid profile email. It does not sign you into the portal.'
        Write-PortalStatus INFO 'The v2 consent link works with an empty configured API-permissions list; it does not request all permissions on a reused registration. Additional API permissions require separate review and consent.'
        Write-PortalStatus INFO 'A consent return to the registered callback is not a portal login. Use the portal sign-in link below afterward; if consent or the return fails, verify the grant in Entra before retrying.'
    } elseif ($TenantId -and $ClientId) {
        Write-PortalStatus INFO 'The consent link is deferred until the selected host URL is known; no default registration callback is assumed.'
    }
    if ($Origin) { Write-PortalStatus ACTION "2. Portal sign-in (after consent and application startup): $Origin" }
    else { Write-PortalStatus ACTION '2. Use the app sign-in flow and review its requested scopes.' }
    Write-PortalStatus ACTION '3. If sign-in requests additional permissions, the authorized administrator selects Consent on behalf of your organization after reviewing them. Basic OIDC scopes are openid, profile and email; reused apps can request more.'
    Write-PortalStatus INFO 'Keep Assignment required enabled. Consent does not give unassigned users access. No consent or extra Graph permissions are granted by this wizard.'
}

function ConvertTo-PortalGuid {
    param([string]$Value)
    $parsed = [guid]::Empty
    if (-not [guid]::TryParse($Value, [ref]$parsed) -or $parsed -eq [guid]::Empty) {
        throw 'Enter a nonzero GUID, not a name or email address.'
    }
    return $parsed.ToString()
}

function Test-PortalAlwaysOnSku {
    param([string]$Sku)
    return $Sku -match '^(B[1-3]|S[1-3]|P[0-9]+(m)?v[2-4]|P[1-3]|I[1-6]v2|I[1-3])$'
}

function Get-PortalHostingReadiness {
    param([hashtable]$Plan, [hashtable]$Web, [string]$NewPlanSku = 'B1', [string]$UpgradePlanSku)
    $currentSku = [string]$Plan.sku.name
    if (-not $Plan) { $currentSku = $NewPlanSku }
    if ($UpgradePlanSku -and ($UpgradePlanSku -notin @('B1','B2','B3','S1','P1v3') -or $currentSku -notin @('F1','D1',$UpgradePlanSku))) {
        throw 'Automatic plan changes are limited to an explicitly selected F1/D1 upgrade; other existing tiers are not resized.'
    }
    $targetSku = if ($UpgradePlanSku) { $UpgradePlanSku } else { $currentSku }
    $supported = Test-PortalAlwaysOnSku $targetSku
    $limitedTier = $targetSku -in @('F1','D1')
    return [pscustomobject]@{
        Check = 'Background jobs / Always On'
        Status = $(if ($limitedTier) { 'Warning' } elseif (-not $supported) { 'Blocked' } elseif (-not $Plan -or $currentSku -ne $targetSku -or $Web.properties.alwaysOn -ne $true) { 'ChangePlanned' } else { 'Ready' })
        CurrentSku = $currentSku; TargetSku = $targetSku
        UpgradeRequired = [bool]($Plan -and $currentSku -ne $targetSku)
        Detail = $(if ($limitedTier) { "Tier '$targetSku' is retained without Always On; preparation and deployment may continue. In-process jobs can be delayed while the app is unloaded; external GitHub workflows are unaffected." }
            elseif (-not $supported) { "Tier '$targetSku' is not supported by this deployment script." }
            elseif ($currentSku -ne $targetSku) { "Upgrade shared plan $currentSku to $targetSku (billable) and enable Always On on this app." }
            elseif ($Web.properties.alwaysOn -ne $true) { 'Enable Always On on this app so idle unload does not suspend background cleanup.' }
            else { 'Always On is enabled on a supported tier.' })
    }
}

function Get-PortalDeploymentPlan {
    param(
        [hashtable]$Snapshot,
        [string]$AppResourceId,
        [string]$ImageReference,
        [string[]]$AdministratorIds
    )
    $hostResource = $Snapshot.Site
    $auth = $Snapshot.Authentication.properties
    $web = $Snapshot.Web.properties
    $settings = $Snapshot.Settings.properties
    $registry = $Snapshot.Registry
    $tenantId = ConvertTo-PortalGuid $Snapshot.Subscription.tenantId
    $subscriptionId = ConvertTo-PortalGuid $Snapshot.Subscription.id
    if ($AppResourceId -notmatch '^/subscriptions/([^/]+)/resourceGroups/[^/]+/providers/Microsoft.Web/sites/[^/]+$' -or
        $Matches[1] -ne $subscriptionId -or $hostResource.id -ne $AppResourceId) {
        throw 'The App Service resource does not match the selected subscription and target.'
    }
    if ($hostResource.kind -notmatch 'linux' -or $hostResource.properties.httpsOnly -ne $true) {
        throw 'The target must be Linux and HTTPS-only.'
    }
    if ($web.linuxFxVersion -like 'sitecontainers*') {
        throw 'Sidecar-enabled hosting is not modified by this script. Use the runbook container configuration steps.'
    }
    if ($Snapshot.Plan.properties.numberOfWorkers -ne 1 -or $Snapshot.Plan.properties.elasticScaleEnabled -eq $true) {
        throw 'The App Service plan must use one instance without automatic scaling.'
    }
    $hostingReadiness = Get-PortalHostingReadiness -Plan $Snapshot.Plan -Web $Snapshot.Web
    if ($hostingReadiness.Status -eq 'Blocked') { throw $hostingReadiness.Detail }
    if ($auth.platform.enabled -ne $true -or $auth.httpSettings.requireHttps -ne $true -or
        $auth.globalValidation.requireAuthentication -ne $true -or $auth.globalValidation.excludedPaths.Count -gt 0 -or
        $auth.globalValidation.unauthenticatedClientAction -ne 'RedirectToLoginPage') {
        throw 'Easy Auth must require HTTPS and authentication on every path, with browser login redirection.'
    }
    $provider = $auth.identityProviders.azureActiveDirectory
    $clientId = ConvertTo-PortalGuid $provider.registration.clientId
    if ($provider.enabled -ne $true -or $provider.registration.openIdIssuer -cne "https://login.microsoftonline.com/$tenantId/v2.0") {
        throw 'The Entra provider must be enabled and use the hosting tenant-specific v2 issuer.'
    }
    if ($auth.globalValidation.redirectToProvider -notin @('aad', 'azureActiveDirectory') -or
        @($provider.validation.allowedAudiences | Where-Object { $_ -ne $clientId }).Count -gt 0) {
        throw 'Select the Microsoft login provider and remove extra audiences in Easy Auth.'
    }
    if ($settings.GALLERY_INSTALLER_ENABLED -in @('true', '1', 'yes')) {
        throw 'The code installer is enabled. Complete or explicitly retire it before sign-in-first deployment.'
    }
    foreach ($key in @('GALLERY_ENTRA_TENANT_ID', 'ARM_TENANT_ID')) {
        if ($settings[$key] -and $settings[$key] -ne $tenantId) { throw 'An existing tenant setting conflicts with the host tenant.' }
    }
    if ($settings.GALLERY_ENTRA_CLIENT_ID -and $settings.GALLERY_ENTRA_CLIENT_ID -ne $clientId) {
        throw 'An existing client-ID setting conflicts with the Entra provider.'
    }
    if ($settings.GALLERY_HOST_RESOURCE_ID -and $settings.GALLERY_HOST_RESOURCE_ID -ne $AppResourceId) {
        throw 'GALLERY_HOST_RESOURCE_ID points at another app. Reconcile the host override before deployment.'
    }
    if ($settings.GALLERY_INVITE_GUEST -in @('true', '1', 'yes') -or $settings.GALLERY_TERRAFORM_AUTO_INSTALL -in @('true', '1', 'yes')) {
        throw 'Disable unsupported guest invitations and automatic Terraform installation before deployment.'
    }
    $identityClient = [string]$settings.AZURE_CLIENT_ID
    $identityMode = 'system'
    $principalId = $hostResource.identity.principalId
    if ($identityClient) {
        $identityMode = 'user'
        $attached = @($hostResource.identity.userAssignedIdentities.Values | Where-Object { $_.clientId -eq $identityClient })
        if ($attached.Count -ne 1) { throw 'AZURE_CLIENT_ID must select one attached user-assigned identity.' }
        $principalId = $attached[0].principalId
    } elseif ($hostResource.identity.type -notmatch 'SystemAssigned') {
        throw 'Enable a system-assigned identity or configure an attached user-assigned identity first.'
    }
    $principalId = ConvertTo-PortalGuid $principalId
    if ($settings.GALLERY_MANAGED_IDENTITY -and $settings.GALLERY_MANAGED_IDENTITY -ne $identityMode) {
        throw 'Existing managed identity mode conflicts with the attached identity configuration.'
    }
    $adminIds = @($AdministratorIds | ForEach-Object { ConvertTo-PortalGuid $_ } | Sort-Object -Unique)
    if ($adminIds.Count -eq 0) { throw 'At least one initial administrator user object ID is required.' }
    $existingAdmins = @(([string]$settings.GALLERY_ADMIN_OIDS).Split(',', [StringSplitOptions]::RemoveEmptyEntries) | ForEach-Object { ConvertTo-PortalGuid $_.Trim() } | Sort-Object -Unique)
    if ($existingAdmins.Count -gt 0 -and ($existingAdmins -join ',') -ne ($adminIds -join ',')) {
        throw 'This deployment cannot replace existing administrator eligibility. Retain the current list and manage roles separately.'
    }
    if ($ImageReference -notmatch '^(?<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[a-f0-9]{64})$') {
        throw 'Use repository:tag or repository@sha256:digest without a registry hostname.'
    }
    $repository = $Matches.repository
    if ($registry.id -notlike "/subscriptions/$subscriptionId/*" -or $registry.loginServer -notmatch '^[a-z0-9][a-z0-9.-]*\.azurecr\.io$' -or
        $registry.adminUserEnabled -eq $true) {
        throw 'Use an organization ACR in the selected subscription with its admin account disabled.'
    }
    if ($Snapshot.Manifest.digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'ACR did not return a valid image digest.' }
    if ($ImageReference.Contains('@') -and $ImageReference.Split('@')[1] -cne $Snapshot.Manifest.digest) {
        throw 'ACR returned a different image digest.'
    }
    $hostname = [string]$hostResource.properties.defaultHostName
    $origin = if ($settings.GALLERY_PUBLIC_URL) { [string]$settings.GALLERY_PUBLIC_URL } else { "https://$hostname" }
    $uri = $null
    if (-not [uri]::TryCreate($origin, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or
        $uri.Port -ne 443 -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or
        $origin.EndsWith('/') -or $uri.Host -notin $hostResource.properties.hostNames) {
        throw 'The public URL setting must be an exact HTTPS origin bound to this App Service.'
    }
    return [pscustomobject]@{
        AppResourceId = $AppResourceId
        TenantId = $tenantId
        EntraClientId = $clientId
        Origin = $origin
        Image = "$($registry.loginServer)/$repository@$($Snapshot.Manifest.digest)"
        PreviousRuntime = [string]$web.linuxFxVersion
        IdentityMode = $identityMode
        IdentityClientId = $identityClient
        IdentityPrincipalId = $principalId
        AdministratorIds = $adminIds -join ','
        RegistryId = $registry.id
        RegistryUrl = "https://$($registry.loginServer)"
        HostingReadiness = $hostingReadiness
    }
}

function Invoke-PortalAzJson {
    param([string[]]$Arguments, [string]$Operation)
    $previousPreference = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
    try {
        $output = & az @Arguments --only-show-errors --output json 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed during $Operation. Check login, permissions and service reachability under organization policy. Remote output withheld." }
        if (-not $output) { return @{} }
        return ($output -join "`n") | ConvertFrom-Json -AsHashtable -ErrorAction Stop
    } catch {
        throw "Azure CLI failed during $Operation. Check login, permissions and service reachability under organization policy. Output withheld."
    } finally {
        $output = $null
        $PSNativeCommandUseErrorActionPreference = $previousPreference
    }
}

function Invoke-PortalArm {
    param(
        [string]$Subscription,
        [string]$Path,
        [ValidateSet('GET', 'POST', 'PUT')][string]$Method = 'GET',
        [hashtable]$Body,
        [string]$ETag
    )
    if ($Path -notmatch '^/subscriptions/[0-9a-f-]{36}/resourceGroups/[^/?#]+/providers/Microsoft\.Web/(sites|serverfarms)/[^?#]+\?api-version=2024-11-01$' -or
        $Path -notlike "/subscriptions/$Subscription/*") { throw 'Unexpected ARM request destination.' }
    $token = Invoke-PortalAzJson -Arguments @('account', 'get-access-token', '--subscription', $Subscription, '--resource', 'https://management.azure.com/') -Operation 'ARM authorization'
    $headers = @{ Authorization = "Bearer $($token.accessToken)" }
    if ($ETag) { $headers['If-Match'] = $ETag }
    $parameters = @{
        Uri = "https://management.azure.com$Path"; Method = $Method; Headers = $headers
        TimeoutSec = 120; MaximumRedirection = 0; ErrorAction = 'Stop'
        ResponseHeadersVariable = 'responseHeaders'
    }
    if ($null -ne $Body) {
        $parameters.ContentType = 'application/json'
        $parameters.Body = ConvertTo-Json -InputObject $Body -Depth 50 -Compress
    }
    try {
        $response = Invoke-RestMethod @parameters
        $result = $response | ConvertTo-Json -Depth 50 | ConvertFrom-Json -AsHashtable
        if ($responseHeaders.ETag) { $result['etag'] = [string]($responseHeaders.ETag | Select-Object -First 1) }
        return $result
    } catch {
        throw "ARM $Method failed. Check access and inspect the target configuration before retrying; remote bodies are withheld to protect secrets."
    } finally {
        $token = $null
        $headers = $null
        $parameters = $null
        $response = $null
    }
}

function Get-PortalSnapshot {
    param([string]$Subscription, [string]$AppResourceId, [string]$Registry, [string]$ImageReference)
    $account = Invoke-PortalAzJson -Arguments @('account', 'show', '--subscription', $Subscription) -Operation 'subscription selection'
    if ($account.id -ne $Subscription -or $account.environmentName -ne 'AzureCloud' -or $account.state -ne 'Enabled') {
        throw 'Select an enabled Azure public-cloud subscription.'
    }
    $site = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId`?api-version=2024-11-01"
    $planId = [string]$site.properties.serverFarmId
    if ($planId -notmatch '^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Web/serverfarms/[^/]+$') {
        throw 'App Service returned an invalid hosting plan ID.'
    }
    return @{
        Subscription = $account
        Site = $site
        Plan = Invoke-PortalArm -Subscription $Subscription -Path "$planId`?api-version=2024-11-01"
        Authentication = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId/config/authsettingsV2?api-version=2024-11-01"
        Web = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId/config/web?api-version=2024-11-01"
        Settings = Invoke-PortalArm -Subscription $Subscription -Path "$AppResourceId/config/appsettings/list?api-version=2024-11-01" -Method POST
        Registry = Invoke-PortalAzJson -Arguments @('acr', 'show', '--name', $Registry, '--subscription', $Subscription) -Operation 'registry discovery'
        Manifest = Invoke-PortalAzJson -Arguments @('acr', 'manifest', 'show-metadata', '--registry', $Registry, '--name', $ImageReference, '--subscription', $Subscription) -Operation 'image digest lookup (authenticated registry access required)'
    }
}

function Test-PortalEntraRegistration {
    param([object]$Plan, [switch]$RegistrationOnly)
    $token = Invoke-PortalAzJson -Arguments @('account', 'get-access-token', '--tenant', $Plan.TenantId, '--resource-type', 'ms-graph') -Operation 'organization Entra read access'
    if ($token.tenant -ne $Plan.TenantId) { throw 'Graph authorization returned the wrong tenant.' }
    try {
        $uri = "https://graph.microsoft.com/v1.0/applications(appId='$($Plan.EntraClientId)')?`$select=appId,signInAudience,web"
        $registration = Invoke-RestMethod -Uri $uri -Headers @{ Authorization = "Bearer $($token.accessToken)" } -TimeoutSec 60 -MaximumRedirection 0 -ErrorAction Stop
    } catch {
        throw 'Cannot read the organization Entra registration. Run as an authorized registration owner/reader; no directory permissions are granted by this script.'
    }
    if ($registration.appId -ne $Plan.EntraClientId -or $registration.signInAudience -ne 'AzureADMyOrg' -or
        $registration.web.implicitGrantSettings.enableIdTokenIssuance -ne $true -or
        "$($Plan.Origin)/.auth/login/aad/callback" -cnotin $registration.web.redirectUris) {
        throw 'Fix the single-tenant Entra registration, exact callback and ID-token issuance checkbox before deploying. No registration was modified.'
    }
    try {
        if (-not $RegistrationOnly) {
            $uri = "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId%20eq%20'$($Plan.EntraClientId)'&`$select=id,appId,appRoleAssignmentRequired"
            $response = Invoke-RestMethod -Uri $uri -Headers @{ Authorization = "Bearer $($token.accessToken)" } -TimeoutSec 60 -MaximumRedirection 0 -ErrorAction Stop
            $principals = @($response.value | Where-Object { $_.appId -eq $Plan.EntraClientId })
            if ($response.'@odata.nextLink' -or $principals.Count -ne 1 -or $principals[0].appRoleAssignmentRequired -ne $true) {
                throw 'Assignment required must be enabled.'
            }
        }
    } catch {
        throw 'Verify Entra Enterprise application > Properties > Assignment required = Yes, and assign only approved employees/groups under Users and groups. Directory read access is required; no access policy was changed.'
    } finally { $token = $null }
}

function Read-PortalDeploymentInput {
    param([string]$Value, [string]$Prompt, [switch]$NonInteractive)
    if ($Value) { return $Value.Trim() }
    if ($NonInteractive) { throw "Missing required input: $Prompt" }
    $answer = Read-Host -Prompt $Prompt
    if (-not $answer.Trim()) { throw "A value is required: $Prompt" }
    return $answer.Trim()
}

function Set-PortalDeployment {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param([object]$Plan, [hashtable]$Snapshot, [string]$Subscription)
    if ($Plan.HostingReadiness.Status -eq 'Warning') { Write-PortalStatus WARN $Plan.HostingReadiness.Detail }
    Write-PortalSection 'APPLY IMAGE | Confirmation required'
    if (-not $PSCmdlet.ShouldProcess($Plan.AppResourceId, "Deploy and start pinned image $($Plan.Image); preserve auth/network/data; settings may restart the site")) {
        return [pscustomobject]@{ Status = 'PreviewOnly'; Url = $Plan.Origin; Image = $Plan.Image }
    }
    $appId = $Plan.AppResourceId
    $freshAuth = Invoke-PortalArm -Subscription $Subscription -Path "$appId/config/authsettingsV2?api-version=2024-11-01"
    $freshWeb = Invoke-PortalArm -Subscription $Subscription -Path "$appId/config/web?api-version=2024-11-01"
    $freshSettings = Invoke-PortalArm -Subscription $Subscription -Path "$appId/config/appsettings/list?api-version=2024-11-01" -Method POST
    $freshSite = Invoke-PortalArm -Subscription $Subscription -Path "$appId`?api-version=2024-11-01"
    foreach ($pair in @(@($freshAuth, $Snapshot.Authentication), @($freshWeb, $Snapshot.Web), @($freshSettings, $Snapshot.Settings), @($freshSite, $Snapshot.Site))) {
        $before = ConvertTo-Json -InputObject $pair[1].properties -Depth 50 -Compress
        $after = ConvertTo-Json -InputObject $pair[0].properties -Depth 50 -Compress
        if ($before -cne $after) { throw 'Target configuration changed after the preview. Rerun to review the current plan; no writes were submitted.' }
    }
    if ((ConvertTo-Json -InputObject $freshSite.identity -Depth 20 -Compress) -cne
        (ConvertTo-Json -InputObject $Snapshot.Site.identity -Depth 20 -Compress)) {
        throw 'The attached managed identity changed after preview. Rerun before applying.'
    }
    $values = $freshSettings.properties
    $values['GALLERY_ADMIN_OIDS'] = $Plan.AdministratorIds
    $values['WEBSITES_ENABLE_APP_SERVICE_STORAGE'] = 'true'
    $values['WEBSITES_PORT'] = '8000'
    $values['GALLERY_INSTALLER_ENABLED'] = 'false'
    $values['GALLERY_EASY_AUTH'] = 'true'
    $values['GALLERY_HTTPS'] = 'true'
    $values['GALLERY_MANAGED_IDENTITY'] = $Plan.IdentityMode
    $values['DOCKER_REGISTRY_SERVER_URL'] = $Plan.RegistryUrl
    $web = $freshWeb.properties
    $web['linuxFxVersion'] = "DOCKER|$($Plan.Image)"
    $web['appCommandLine'] = ''
    $enableAlwaysOn = Test-PortalAlwaysOnSku $Snapshot.Plan.sku.name
    $web['alwaysOn'] = $enableAlwaysOn
    $web['acrUseManagedIdentityCreds'] = $true
    $web['acrUserManagedIdentityID'] = if ($Plan.IdentityMode -eq 'user') { $Plan.IdentityClientId } else { '' }
    try {
        $null = Invoke-PortalArm -Subscription $Subscription -Path "$appId/config/appsettings?api-version=2024-11-01" -Method PUT -Body @{ properties = $values } -ETag $freshSettings.etag
        $null = Invoke-PortalArm -Subscription $Subscription -Path "$appId/config/web?api-version=2024-11-01" -Method PUT -Body @{ properties = $web } -ETag $freshWeb.etag
        $observed = Invoke-PortalArm -Subscription $Subscription -Path "$appId/config/web?api-version=2024-11-01"
        $observedSettings = Invoke-PortalArm -Subscription $Subscription -Path "$appId/config/appsettings/list?api-version=2024-11-01" -Method POST
        if ($observed.properties.linuxFxVersion -cne $web.linuxFxVersion -or $observed.properties.appCommandLine -or [bool]$observed.properties.alwaysOn -ne $enableAlwaysOn -or
            $observed.properties.acrUseManagedIdentityCreds -ne $true -or
            [string]$observed.properties.acrUserManagedIdentityID -cne [string]$web.acrUserManagedIdentityID -or
            $observedSettings.properties.GALLERY_ADMIN_OIDS -ne $values.GALLERY_ADMIN_OIDS -or
            $observedSettings.properties.WEBSITES_PORT -ne '8000' -or
            $observedSettings.properties.WEBSITES_ENABLE_APP_SERVICE_STORAGE -ne 'true' -or
            $observedSettings.properties.GALLERY_INSTALLER_ENABLED -ne 'false' -or
            $observedSettings.properties.GALLERY_MANAGED_IDENTITY -ne $Plan.IdentityMode -or
            $observedSettings.properties.DOCKER_REGISTRY_SERVER_URL -ne $Plan.RegistryUrl -or
            $observedSettings.properties.GALLERY_EASY_AUTH -ne 'true' -or
            $observedSettings.properties.GALLERY_HTTPS -ne 'true') { throw 'Configuration readback did not match.' }
        $null = Invoke-PortalAzJson -Arguments @('webapp', 'start', '--subscription', $Subscription, '--ids', $appId) -Operation 'start configured container app'
    } catch {
        throw 'Deployment did not finish or could not be verified. One settings write may have succeeded. Inspect the app before retrying; do not delete data or enable public access. Use your protected backup for reviewed rollback.'
    }
    $health = Test-PortalDeploymentHealth -Origin $Plan.Origin
    return [pscustomobject]@{
        Status = 'ConfigurationApplied'
        Url = $Plan.Origin
        Image = $Plan.Image
        PreviousRuntime = $Plan.PreviousRuntime
        RuntimeVerified = $false
        HealthObservation = $health.Status
        HealthDetail = $health.Detail
        SignInVerified = $false
    }
}

function Test-PortalDeploymentHealth {
    param([string]$Origin)
    $uri = $null
    if (-not [Uri]::TryCreate($Origin,[UriKind]::Absolute,[ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.Port -ne 443 -or
        $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or $Origin.EndsWith('/')) { throw 'Use a validated HTTPS host origin for health verification.' }
    try {
        $response = Invoke-WebRequest -Uri "$Origin/healthz" -Method GET -TimeoutSec 30 -MaximumRedirection 0 -SkipHttpErrorCheck -ErrorAction Stop
        $status = [int]$response.StatusCode
        if ($status -eq 200) {
            $body = $response.Content | ConvertFrom-Json -ErrorAction Stop
            if ($body.status -eq 'ok') { return @{ Status='HealthyResponse'; Detail='The application health endpoint responded. Image identity and employee sign-in still need verification.' } }
        }
        if ($status -in @(301,302,303,307,308,401,403)) { return @{ Status='SignInRequired'; Detail='Authentication or organization access policy prevents an anonymous health check. Verify in your own signed-in browser; no bypass was attempted.' } }
        return @{ Status='PendingOrFailed'; Detail="Health returned HTTP $status. Check container startup logs and rerun only the health check after startup completes." }
    } catch { return @{ Status='Unverified'; Detail='Health could not be verified within the bounded request. Inspect startup logs and organization access policy; remote response content withheld.' } }
}

function Invoke-PortalDeployment {
    <#
    .SYNOPSIS
    Prompts for an existing target, validates it and submits only confirmed configuration changes.
    .EXAMPLE
    Invoke-PortalDeployment -WhatIf
    #>
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param(
        [string]$SubscriptionId, [string]$ResourceGroupName, [string]$AppName,
        [string]$RegistryName, [string]$Image, [string[]]$AdminObjectIds,
        [switch]$NonInteractive
    )
    $ErrorActionPreference = 'Stop'
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw 'Install Azure CLI first, or use Azure Cloud Shell PowerShell.' }
    Write-PortalSection 'DEPLOYMENT | Validate the published image and host'
    Write-PortalStatus INFO 'Existing resources only. No build, resource creation or Entra changes in this phase.'
    Write-PortalStatus INFO 'Registry access is authenticated; organization network restrictions remain unchanged.'
    $SubscriptionId = ConvertTo-PortalGuid (Read-PortalDeploymentInput $SubscriptionId 'Subscription ID' -NonInteractive:$NonInteractive)
    $ResourceGroupName = Read-PortalDeploymentInput $ResourceGroupName 'Existing App Service resource group' -NonInteractive:$NonInteractive
    $AppName = Read-PortalDeploymentInput $AppName 'Existing App Service name' -NonInteractive:$NonInteractive
    $RegistryName = Read-PortalDeploymentInput $RegistryName 'Existing ACR resource name (same subscription)' -NonInteractive:$NonInteractive
    $Image = Read-PortalDeploymentInput $Image 'Published image repository:tag or repository@sha256:digest (no registry hostname)' -NonInteractive:$NonInteractive
    if ($ResourceGroupName -notmatch '^[A-Za-z0-9_().-]{1,90}$' -or $ResourceGroupName.EndsWith('.') -or
        $AppName -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$' -or
        $RegistryName -notmatch '^[A-Za-z0-9]{5,50}$' -or
        $Image -notmatch '^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}|@sha256:[a-f0-9]{64})$') {
        throw 'Invalid resource name or image reference. Use literal resource names and a published tag or digest.'
    }
    if (-not $AdminObjectIds) {
        $inputIds = Read-PortalDeploymentInput '' 'Initial administrator USER object IDs (comma-separated, not email addresses)' -NonInteractive:$NonInteractive
        $AdminObjectIds = $inputIds.Split(',')
    }
    $AdminObjectIds = @($AdminObjectIds | ForEach-Object { ConvertTo-PortalGuid $_.Trim() })
    $resourceId = "/subscriptions/$SubscriptionId/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/sites/$AppName"
    $snapshot = Get-PortalSnapshot -Subscription $SubscriptionId -AppResourceId $resourceId -Registry $RegistryName -ImageReference $Image
    $plan = Get-PortalDeploymentPlan -Snapshot $snapshot -AppResourceId $resourceId -ImageReference $Image -AdministratorIds $AdminObjectIds
    Test-PortalEntraRegistration -Plan $plan
    Write-PortalConsentAction -TenantId $plan.TenantId -ClientId $plan.EntraClientId -Origin $plan.Origin
    Write-PortalSection 'DEPLOYMENT PLAN | Review before confirmation'
    Write-PortalStatus INFO "Target: $($plan.AppResourceId)"
    Write-PortalStatus INFO "Image (pinned digest): $($plan.Image)"
    Write-PortalStatus INFO "Previous runtime: $($plan.PreviousRuntime)"
    Write-PortalStatus INFO "Managed identity: $($plan.IdentityMode); principal: $($plan.IdentityPrincipalId)"
    Write-PortalStatus PLAN 'After approval, the script will pin the image, clear the old startup command, enable managed-identity pulls and set port/persistent storage; explicit admins are retained.'
    Write-PortalStatus INFO 'Unchanged: networking, authentication provider/secrets, Azure roles, databases and repository setup.'
    Write-PortalStatus WARN 'Verify backups, image security/architecture and UID 10001 storage access. These are not certified by this script.'
    $apply = @{ Plan = $plan; Snapshot = $snapshot; Subscription = $SubscriptionId; WhatIf = [bool]$WhatIfPreference }
    if ($PSBoundParameters.ContainsKey('Confirm')) { $apply.Confirm = $PSBoundParameters.Confirm }
    if ($NonInteractive -and -not $WhatIfPreference -and (-not $PSBoundParameters.ContainsKey('Confirm') -or $PSBoundParameters.Confirm)) {
        throw 'For unattended apply, provide all parameters and explicitly pass -Confirm:$false after reviewing -WhatIf and backups.'
    }
    $result = Set-PortalDeployment @apply
    if ($result.Status -eq 'ConfigurationApplied') {
        Write-PortalSection 'DEPLOYMENT RESULT | Configuration applied, verification pending'
        Write-PortalStatus OK 'Azure accepted the image configuration, readback matched, and the start command succeeded.'
        $healthStatus = if ($result.HealthObservation -eq 'HealthyResponse') { 'OK' } else { 'WARN' }
        Write-PortalStatus $healthStatus "Health observation: $($result.HealthObservation). $($result.HealthDetail)"
        Write-PortalStatus WARN 'Running image identity, administrator consent and employee sign-in are NOT VERIFIED. ConfigurationApplied does not mean onboarding is complete.'
        Write-PortalConsentAction -TenantId $plan.TenantId -ClientId $plan.EntraClientId -Origin $plan.Origin
        Write-PortalSection 'NEXT ACTIONS | Complete verification'
        Write-PortalStatus ACTION '1. App Service > Deployment Center / Log stream: confirm the pinned image starts without errors.'
        Write-PortalStatus ACTION "2. After administrator consent, open $($plan.Origin) and sign in as the assigned initial administrator."
        Write-PortalStatus ACTION '3. Complete browser Setup: organization GitHub connection/repository and deployment subscriptions.'
        Write-PortalStatus INFO 'An unverified health check alone is not a reason to rebuild or redeploy. Check startup and access policy first; do not bypass authentication.'
    } else {
        Write-PortalSection 'DEPLOYMENT RESULT | No image changes applied'
        Write-PortalStatus INFO 'Preview or declined confirmation. The selected image was not deployed.'
    }
    Write-PortalSection 'RESULT DATA | PowerShell return object'
    return $result
}

if ($MyInvocation.InvocationName -ne '.') {
    $deploymentParameters = @{} + $PSBoundParameters
    $deploymentParameters.Remove('Mode')
    if (-not $Mode -and -not $NonInteractive) {
        $choice = Read-Host 'Choose workflow: 1 = prepare/create Azure prerequisites, 2 = deploy an already-published image'
        if ($choice -notin @('1','2')) { throw 'Choose 1 or 2.' }
        $Mode = if ($choice -eq '1') { 'Prepare' } else { 'Deploy' }
    }
    if ($Mode -eq 'Prepare') {
        $deploymentParameters.Remove('Image')
        & "$PSScriptRoot/Initialize-Portal.ps1" @deploymentParameters
    } else {
        if ($deploymentParameters.ContainsKey('ConfigPath') -or $deploymentParameters.ContainsKey('Fresh')) {
            throw '-ConfigPath and -Fresh apply only to -Mode Prepare.'
        }
        Invoke-PortalDeployment @deploymentParameters
    }
}