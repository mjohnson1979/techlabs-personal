#Requires -Version 7.0

function Read-PortalFoundationValue {
    param([string]$Prompt, [string]$Default)
    $label = if ($Default) { "$Prompt (Enter = $Default)" } else { $Prompt }
    $value = Read-Host $label
    if ([string]::IsNullOrWhiteSpace($value)) {
        if ($Default) { return $Default }
        throw "Missing required input: $Prompt"
    }
    return $value.Trim()
}

function Read-PortalChoice {
    param([string]$Prompt, [object[]]$Options = @(), [string]$Default, [switch]$AllowNew, [string]$NewDefault)
    $optionsList = @($Options | Where-Object { $null -ne $_ })
    $defaultHint = $Default
    Write-Host ''
    Write-Host "[SELECT] $Prompt" -ForegroundColor Cyan
    for ($index = 0; $index -lt $optionsList.Count; $index++) {
        $marker = if ($optionsList[$index].Value -eq $Default) { ' [default]' } else { '' }
        if ($marker) { $defaultHint = "option $($index + 1)" }
        $color = if ($marker) { 'Green' } else { 'Gray' }
        Write-Host "  $($index + 1). $($optionsList[$index].Label)$marker" -ForegroundColor $color
    }
    if ($AllowNew) { Write-Host "  N. Create new (suggested: $NewDefault)" -ForegroundColor Magenta }
    while ($true) {
        $hint = if ($Default) { "Enter = $defaultHint" } elseif ($AllowNew) { "Enter = new $NewDefault" } else { 'select a number or value' }
        $answer = [string](Read-Host "$Prompt [$hint; Q = cancel]")
        $answer = $answer.Trim()
        if ($answer -eq 'Q') { throw 'Wizard cancelled; no further actions submitted.' }
        if (-not $answer) { $answer = if ($Default) { $Default } elseif ($AllowNew) { 'N' } else { '' } }
        if ($AllowNew -and $answer -eq 'N') {
            $answer = Read-PortalFoundationValue "New $Prompt name" $NewDefault
            if ($answer -eq 'Q') { throw 'Wizard cancelled; no further actions submitted.' }
            if ($answer -in $optionsList.Value) { Write-Warning 'That name already exists. Choose a different new name or explicitly select the existing resource.'; continue }
        }
        $number = 0
        if ([int]::TryParse($answer, [ref]$number)) {
            if ($number -ge 1 -and $number -le $optionsList.Count) { return [string]$optionsList[$number - 1].Value }
        } else {
            $matching = @($optionsList | Where-Object { $_.Value -eq $answer })
            if ($matching.Count -eq 1) { return [string]$matching[0].Value }
            if ($AllowNew -and $answer -match '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$') { return $answer }
        }
        Write-Warning 'Choose a listed number/value or a valid new resource name. Q cancels.'
    }
}

function ConvertTo-PortalLocation {
    param([string]$Value, [object[]]$Locations)
    $matches = @($Locations | Where-Object { $_.name -eq $Value.Trim() -or $_.displayName -eq $Value.Trim() })
    if ($matches.Count -ne 1) { throw 'Choose an Azure region name or display name from the listed locations.' }
    return [string]$matches[0].name
}

function Get-PortalNewResourceName {
    param([string]$Suggested, [string[]]$ExistingNames)
    $candidate = $Suggested
    $number = 2
    while ($candidate -in $ExistingNames) {
        $candidate = "$($Suggested.Substring(0,[Math]::Min(54,$Suggested.Length)).TrimEnd('-'))-$number"
        $number++
    }
    return $candidate
}

function Read-PortalResourceChoice {
    param([string]$Prompt, [object[]]$Resources, [string]$Name, [string]$ResourceId, [string]$PreferredGroup, [string]$NewDefault, [string]$DefaultResourceId)
    $available = @($Resources | Where-Object { $null -ne $_ } | Sort-Object name,id)
    $options = @($available | ForEach-Object { @{ Value=$_.id; Label="$($_.name) | group $($_.id.Split('/')[4]) | $($_.location)" } })
    $choice = $ResourceId
    if (-not $choice -and $Name) {
        $matching = @($available | Where-Object { $_.name -eq $Name -or $_.id -eq $Name })
        if ($matching.Count -eq 1) { $choice = $matching[0].id }
        elseif ($matching.Count -gt 1) {
            $choice = Read-PortalChoice "$Prompt (name exists in multiple groups)" @($options | Where-Object { $_.Value -in $matching.id })
        } else { $choice = $Name }
    }
    if (-not $choice) {
        $local = @($available | Where-Object { $_.id.Split('/')[4] -eq $PreferredGroup })
        $default = if ($DefaultResourceId -and $DefaultResourceId -in $available.id) { $DefaultResourceId }
            elseif ($local.Count -eq 1) { $local[0].id } elseif ($available.Count -eq 1) { $available[0].id } else { '' }
        $suggested = Get-PortalNewResourceName $NewDefault @($available.name)
        $choice = Read-PortalChoice $Prompt $options $default -AllowNew -NewDefault $suggested
    }
    $matching = @($available | Where-Object { $_.id -eq $choice -or $_.name -eq $choice })
    if ($matching.Count -gt 1) {
        $choice = Read-PortalChoice "$Prompt (choose resource group)" @($options | Where-Object { $_.Value -in $matching.id })
        $matching = @($available | Where-Object { $_.id -eq $choice })
    }
    if ($matching.Count -eq 1) { return @{ Name=$matching[0].name; Id=$matching[0].id; Group=$matching[0].id.Split('/')[4] } }
    if ($ResourceId -or $choice -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$') { throw "Selected $Prompt was not found in this subscription. No replacement will be created." }
    return @{ Name=$choice; Id=''; Group=$PreferredGroup }
}

function Get-PortalTenantRegistrations {
    param([string]$TenantId)
    $path = '/applications?$filter=signInAudience%20eq%20%27AzureADMyOrg%27&$select=appId,displayName,signInAudience,web,tags&$top=100'
    $applications = @()
    for ($page = 0; $path; $page++) {
        if ($page -ge 20) { throw 'Registration listing exceeds 2000 entries. Use an explicit client ID instead.' }
        $response = Invoke-PortalFoundationGraph $TenantId $path
        $applications += @($response.value | Where-Object { $_.signInAudience -eq 'AzureADMyOrg' })
        $next = [string]$response.'@odata.nextLink'
        if ($next -and -not $next.StartsWith('https://graph.microsoft.com/v1.0/applications?', [StringComparison]::Ordinal)) { throw 'Unexpected directory pagination destination.' }
        $path = if ($next) { $next.Substring('https://graph.microsoft.com/v1.0'.Length) } else { '' }
    }
    return $applications | Sort-Object displayName,appId
}

function Read-PortalManagedIdentityChoice {
    param([hashtable]$Inputs, [object[]]$Resources, [hashtable]$Site, [hashtable]$Settings)
    $selectedClient = [string]$Settings.AZURE_CLIENT_ID
    if ($selectedClient) {
        $attached = @($Site.identity.userAssignedIdentities.Keys | Where-Object { $Site.identity.userAssignedIdentities[$_].clientId -eq $selectedClient })
        if ($attached.Count -ne 1) { throw 'The configured user-assigned identity is not attached uniquely; reconcile the existing app before continuing.' }
        if ($Inputs.NewManagedIdentityName -or ($Inputs.ManagedIdentityResourceId -and $Inputs.ManagedIdentityResourceId -ne $attached[0])) { throw 'The configured host identity will not be replaced.' }
        $Inputs.ManagedIdentityResourceId = $attached[0]
        Write-PortalStatus INFO "Retain configured managed identity: $($attached[0]) | client $selectedClient."
        return
    }
    if ($Settings.GALLERY_MANAGED_IDENTITY -eq 'system') {
        if ($Inputs.ManagedIdentityResourceId -or $Inputs.NewManagedIdentityName) { throw 'The configured system host identity will not be replaced.' }
        Write-PortalStatus INFO 'Retain the app-configured system-assigned managed identity.'
        return
    }
    if ($Inputs.ManagedIdentityResourceId -or $Inputs.NewManagedIdentityName) { return }
    $identities = @($Resources | Where-Object { $_.type -eq 'Microsoft.ManagedIdentity/userAssignedIdentities' } | Sort-Object name,id)
    $options = @(@{ Value='System'; Label=$(if ($Site.identity.principalId) { "Reuse this app's system identity | $($Site.identity.principalId)" } else { 'Create/enable a system-assigned identity for this app' }) })
    $options += @($identities | ForEach-Object { @{ Value=$_.id; Label="Reuse $($_.name) | group $($_.id.Split('/')[4]) | $($_.location)" } })
    $options += @{ Value='New'; Label='Create a named user-assigned managed identity in the hosting group' }
    $choice = Read-PortalChoice 'Host managed identity' $options 'System'
    if ($choice -eq 'New') {
        $suffix = $Inputs.AppName -replace '^app-',''
        $suggested = Get-PortalNewResourceName "id-$suffix" @($identities.name)
        $Inputs.NewManagedIdentityName = Read-PortalFoundationValue 'New managed identity name' $suggested
    } elseif ($choice -ne 'System') { $Inputs.ManagedIdentityResourceId = $choice }
}

function Get-PortalDiscoveredInputs {
    param([hashtable]$Values)
    $inputs = @{} + $Values
    $current = Invoke-PortalAzJson -Arguments @('account','show') -Operation 'current Azure CLI login selection'
    if ($current.state -ne 'Enabled' -or $current.environmentName -ne 'AzureCloud' -or
        -not $current.user.name -or -not $current.user.type) {
        throw 'The current Azure CLI account is not an enabled public-cloud selection with a known identity. Check az account show in this same terminal; cached accounts will not be used as a fallback.'
    }
    $currentId = ConvertTo-PortalGuid $current.id
    $currentTenant = ConvertTo-PortalGuid $current.tenantId
    $subscriptions = @(Invoke-PortalAzJson -Arguments @('account','list') -Operation 'subscriptions for the current Azure CLI identity' |
        Where-Object { $_.state -eq 'Enabled' -and $_.cloudName -eq 'AzureCloud' -and
            $_.tenantId -eq $currentTenant -and $_.user.name -eq $current.user.name -and
            $_.user.type -eq $current.user.type -and $_.id -ne $currentId } | Sort-Object name,id)
    $subscriptions = @($current) + $subscriptions
    Write-PortalStatus INFO "Active Azure CLI identity: $($current.user.name) ($($current.user.type)); tenant $currentTenant."
    Write-PortalStatus INFO "Current subscription: $($current.name) | $currentId. Only subscriptions for this identity and tenant are offered."
    if (-not $inputs.SubscriptionId) {
        $options = @($subscriptions | ForEach-Object { @{ Value=$_.id; Label="$($_.name) | $($_.id) | tenant $($_.tenantId)" } })
        $inputs.SubscriptionId = Read-PortalChoice 'Subscription' $options $currentId
    }
    $inputs.SubscriptionId = ConvertTo-PortalGuid $inputs.SubscriptionId
    $selected = @($subscriptions | Where-Object { $_.id -eq $inputs.SubscriptionId })
    if ($selected.Count -ne 1) { throw 'The supplied subscription does not belong to the current Azure CLI identity and tenant. Select the intended login in this same terminal, then rerun; another cached account will not be used.' }
    $account = $selected[0]
    if ($inputs.SubscriptionId -ne $currentId) {
        $account = Invoke-PortalAzJson -Arguments @('account','show','--subscription',$inputs.SubscriptionId) -Operation 'selected subscription identity verification'
        if ($account.id -ne $inputs.SubscriptionId -or $account.tenantId -ne $currentTenant -or
            $account.user.name -ne $current.user.name -or $account.user.type -ne $current.user.type -or
            $account.state -ne 'Enabled' -or $account.environmentName -ne 'AzureCloud') {
            throw 'The selected subscription resolves to a different CLI identity, tenant or cloud. No resource discovery was performed.'
        }
    }
    Write-PortalStatus INFO "Subscription: $($account.name) | $($account.id) | tenant $($account.tenantId). CLI default will not be changed."
    $groups = @(Invoke-PortalAzJson -Arguments @('group','list','--subscription',$inputs.SubscriptionId) -Operation 'resource groups')
    if (-not $inputs.ResourceGroupName) {
        $options = @($groups | Sort-Object name | ForEach-Object { @{ Value=$_.name; Label="$($_.name) | $($_.location)" } })
        $inputs.ResourceGroupName = Read-PortalChoice 'Resource group' $options $(if ($groups.Count -eq 1) { $groups[0].name }) -AllowNew -NewDefault 'rg-deploymentportal-test-001'
    }
    if ($inputs.ResourceGroupName -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$') { throw 'Invalid resource group name. Use 2-60 alphanumeric/hyphen characters.' }
    $group = @($groups | Where-Object { $_.name -eq $inputs.ResourceGroupName })
    $groupId = "/subscriptions/$($inputs.SubscriptionId)/resourceGroups/$($inputs.ResourceGroupName)"
    if ($group.Count) { Write-PortalStatus INFO "Selected resource group $($inputs.ResourceGroupName)." }
    else { Write-PortalStatus INFO "Resource group $($inputs.ResourceGroupName) is the proposed destination for new hosting." }
    Write-PortalStatus INFO 'Discovering existing services across this subscription, including other resource groups.'
    $resources = @(Invoke-PortalAzJson -Arguments @('resource','list','--subscription',$inputs.SubscriptionId) -Operation 'subscription-wide hosting and identity discovery')
    foreach ($resource in $resources) {
        if ($resource.id -notlike "/subscriptions/$($inputs.SubscriptionId)/resourceGroups/*/providers/*") { throw 'Resource discovery returned an out-of-scope ID.' }
    }
    $suffix = ($inputs.ResourceGroupName -replace '^rg-', '')
    if ($suffix.Length -gt 45) { $suffix = $suffix.Substring(0,45).TrimEnd('-') }
    $apps = @($resources | Where-Object { $_.type -eq 'Microsoft.Web/sites' -and $_.kind -match 'linux' } | Sort-Object name)
    $appChoice = Read-PortalResourceChoice 'Web app' $apps -Name $inputs.AppName -PreferredGroup $inputs.ResourceGroupName -NewDefault "app-$suffix"
    $inputs.AppName = $appChoice.Name
    if ($appChoice.Id -and $appChoice.Group -ne $inputs.ResourceGroupName) {
        $reuse = Read-PortalChoice 'Use existing app in its original resource group?' @(@{ Value='Yes'; Label="Yes, use $($appChoice.Id); do not move it" }, @{ Value='No'; Label='No, cancel and choose a new app name' }) 'Yes'
        if ($reuse -ne 'Yes') { throw 'Existing app retained. Choose a new app name to create hosting in another group.' }
        $inputs.ResourceGroupName = $appChoice.Group
        $groupId = "/subscriptions/$($inputs.SubscriptionId)/resourceGroups/$($inputs.ResourceGroupName)"
        $group = @($groups | Where-Object { $_.name -eq $inputs.ResourceGroupName })
        Write-PortalStatus INFO "Hosting group is now $($inputs.ResourceGroupName); the earlier new group will not be created."
    }
    if ($inputs.AppName -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$') { throw 'Invalid web app name.' }
    $appId = "$groupId/providers/Microsoft.Web/sites/$($inputs.AppName)"
    $site = Invoke-PortalPrerequisiteResource $inputs.SubscriptionId $appId '2024-11-01' -AllowMissing
    $settings = @{}
    $auth = @{}
    $web = @{}
    $plans = @($resources | Where-Object { $_.type -eq 'Microsoft.Web/serverfarms' -and $_.kind -match 'linux' } | Sort-Object name)
    if ($site) {
        if ($site.kind -notmatch 'linux') { throw 'This existing app is not Linux. Select a compatible app or create a new one.' }
        $linkedPlan = [string]$site.properties.serverFarmId
        $planName = $linkedPlan.Split('/')[-1]
        if (($inputs.PlanName -and $inputs.PlanName -ne $planName) -or ($inputs.PlanResourceId -and $inputs.PlanResourceId -ne $linkedPlan)) { throw 'The supplied plan differs from the app plan. Existing apps are never moved between plans.' }
        $inputs.PlanName = $planName
        $inputs.PlanResourceId = $linkedPlan
        $null = Get-PortalHostingResourceId $inputs Plan
        $settings = (Invoke-PortalArm -Subscription $inputs.SubscriptionId -Path "$appId/config/appsettings/list?api-version=2024-11-01" -Method POST).properties
        $auth = (Invoke-PortalArm -Subscription $inputs.SubscriptionId -Path "$appId/config/authsettingsV2?api-version=2024-11-01").properties
        $web = (Invoke-PortalArm -Subscription $inputs.SubscriptionId -Path "$appId/config/web?api-version=2024-11-01").properties
        Write-PortalStatus INFO "Reuse app $($inputs.AppName); attached plan $planName; region $($site.location)."
    } else {
        $planChoice = Read-PortalResourceChoice 'App Service plan' $plans -Name $inputs.PlanName -ResourceId $inputs.PlanResourceId -PreferredGroup $inputs.ResourceGroupName -NewDefault "asp-$suffix"
        $inputs.PlanName = $planChoice.Name
        $inputs.PlanResourceId = $planChoice.Id
    }
    if ($inputs.PlanName -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,58}[A-Za-z0-9]$') { throw 'Invalid App Service plan name.' }
    $plan = Invoke-PortalPrerequisiteResource $inputs.SubscriptionId (Get-PortalHostingResourceId $inputs Plan) '2024-11-01' -AllowMissing
    $locations = @((Invoke-PortalPrerequisiteResource $inputs.SubscriptionId "/subscriptions/$($inputs.SubscriptionId)/locations" '2022-12-01').value)
    $fixedLocation = if ($site) { $site.location } elseif ($plan) { $plan.location } else { '' }
    if ($inputs.Location) { $inputs.Location = ConvertTo-PortalLocation $inputs.Location $locations }
    if ($fixedLocation) {
        $fixedLocation = ConvertTo-PortalLocation $fixedLocation $locations
        if ($inputs.Location -and $inputs.Location -ne $fixedLocation) { throw 'The requested region conflicts with the existing app/plan. Existing resources are not moved.' }
        $inputs.Location = $fixedLocation
        Write-PortalStatus INFO "Hosting region: $fixedLocation (from existing hosting)."
    } elseif (-not $inputs.Location) {
        $options = @($locations | Sort-Object displayName | ForEach-Object { @{ Value=$_.name; Label="$($_.displayName) ($($_.name))" } })
        $defaultRegion = if ($group.Count) { $group[0].location } else { '' }
        while (-not $inputs.Location) {
            $options | ForEach-Object { Write-Host "  $($_.Label)" -ForegroundColor Gray }
            $answer = Read-PortalFoundationValue 'Azure region (name or display name)' $defaultRegion
            if ($answer -eq 'Q') { throw 'Wizard cancelled.' }
            try { $inputs.Location = ConvertTo-PortalLocation $answer $locations }
            catch { Write-Warning 'Unknown region. Enter a listed name such as centralus or Central US.' }
        }
    }
    if ($plan) {
        Write-PortalStatus INFO "Plan SKU: $($plan.sku.name)."
        if ($plan.sku.name -in @('F1','D1') -and -not $inputs.UpgradePlanSku) {
            $upgrade = Read-PortalChoice 'Optional Always On upgrade' @(
                @{ Value='Upgrade'; Label='Plan an upgrade to a paid tier; review shared-app impact and confirm before applying' },
                @{ Value='Keep'; Label='Keep this tier and continue without Always On; in-process jobs may be delayed while idle' }) 'Keep'
            if ($upgrade -eq 'Upgrade') {
                $inputs.UpgradePlanSku = Read-PortalChoice 'Paid plan upgrade SKU' @('B1','B2','B3','S1','P1v3' | ForEach-Object { @{ Value=$_; Label=$_ } }) 'B1'
            }
        }
    }
    elseif (-not $Values.PlanSkuProvided) {
        $inputs.PlanSku = Read-PortalChoice 'New Linux plan SKU' @('B1','B2','B3','S1','P1v3','F1' | ForEach-Object {
            @{ Value=$_; Label=$(if ($_ -eq 'F1') { 'F1 (Free; no Always On)' } else { $_ }) }
        }) 'B1'
    }
    $registries = @($resources | Where-Object { $_.type -eq 'Microsoft.ContainerRegistry/registries' } | Sort-Object name)
    $registryHost = ([string]$settings.DOCKER_REGISTRY_SERVER_URL -replace '^https?://','').TrimEnd('/')
    if (-not $registryHost -and $web.linuxFxVersion -match '^DOCKER\|([^/]+)/') { $registryHost = $Matches[1] }
    $attachedRegistry = @($registries | Where-Object { $registryHost -eq "$($_.name).azurecr.io" -or $registryHost -eq $_.properties.loginServer })
    if (-not $inputs.RegistryName) {
        $hasher = [Security.Cryptography.SHA256]::Create()
        try { $hash = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($groupId.ToLowerInvariant()))) -replace '-','').Substring(0,6).ToLowerInvariant() }
        finally { $hasher.Dispose() }
        $registrySuffix = ($suffix.ToLowerInvariant() -replace '[^a-z0-9]','')
        if ($registrySuffix.Length -gt 40) { $registrySuffix = $registrySuffix.Substring(0,40) }
        $registryChoice = Read-PortalResourceChoice 'Registry' $registries -ResourceId $inputs.RegistryResourceId -PreferredGroup $inputs.ResourceGroupName -NewDefault "cr$registrySuffix$hash" -DefaultResourceId $(if ($attachedRegistry.Count -eq 1) { $attachedRegistry[0].id })
    } else {
        $registryChoice = Read-PortalResourceChoice 'Registry' $registries -Name $inputs.RegistryName -ResourceId $inputs.RegistryResourceId -PreferredGroup $inputs.ResourceGroupName
    }
    $inputs.RegistryName = $registryChoice.Name
    $inputs.RegistryResourceId = $registryChoice.Id
    if ($inputs.RegistryName -notmatch '^[a-z0-9]{5,50}$') { throw 'Registry names must be 5-50 lowercase alphanumeric characters.' }
    if ($inputs.RegistryName -notin $registries.name) {
        $availability = Invoke-PortalAzJson -Arguments @('acr','check-name','--subscription',$inputs.SubscriptionId,'--name',$inputs.RegistryName) -Operation 'new registry name availability'
        while ($availability.nameAvailable -ne $true) {
            if ($Values.RegistryName) { throw 'The supplied registry name is unavailable and was not found in this subscription. Select an accessible registry or a new name.' }
            Write-Warning 'That globally unique registry name is unavailable. Choose another name.'
            $inputs.RegistryName = Read-PortalChoice 'Registry' -AllowNew -NewDefault "$($inputs.RegistryName.Substring(0,[Math]::Min(44,$inputs.RegistryName.Length)))$([Guid]::NewGuid().ToString('N').Substring(0,6))"
            if ($inputs.RegistryName -notmatch '^[a-z0-9]{5,50}$') { Write-Warning 'Use 5-50 lowercase alphanumeric characters.'; continue }
            $availability = Invoke-PortalAzJson -Arguments @('acr','check-name','--subscription',$inputs.SubscriptionId,'--name',$inputs.RegistryName) -Operation 'new registry name availability'
        }
    }
    Read-PortalManagedIdentityChoice -Inputs $inputs -Resources $resources -Site $site -Settings $settings
    if (-not $inputs.AdminObjectIds) {
        if ($settings.GALLERY_ADMIN_OIDS) {
            $inputs.AdminObjectIds = @(([string]$settings.GALLERY_ADMIN_OIDS).Split(',') | ForEach-Object { ConvertTo-PortalGuid $_.Trim() })
            Write-PortalStatus INFO "Existing administrator eligibility retained: $($inputs.AdminObjectIds -join ', ')."
        } else {
            $suggestedAdmin = ''
            try {
                $me = Invoke-PortalFoundationGraph $account.tenantId '/me?$select=id,displayName,userType,accountEnabled'
                if ($me.userType -eq 'Member' -and $me.accountEnabled -eq $true) {
                    $suggestedAdmin = ConvertTo-PortalGuid $me.id
                    Write-PortalStatus INFO "Signed-in member: $($me.displayName) | $suggestedAdmin. Confirm initial administrator eligibility below."
                }
            } catch { Write-Warning 'The signed-in member could not be resolved through Graph. Enter the intended administrator user object ID explicitly.' }
            $inputs.AdminObjectIds = (Read-PortalFoundationValue 'Initial administrator user object IDs (comma-separated)' $suggestedAdmin).Split(',')
        }
    }
    $inputs.AdminObjectIds = @($inputs.AdminObjectIds | ForEach-Object { ConvertTo-PortalGuid $_.Trim() })
    $configuredClient = [string]$auth.identityProviders.azureActiveDirectory.registration.clientId
    $candidates = @()
    $entraMode = $Values.EntraMode
    $entraClient = $Values.EntraClientId
    if ($configuredClient) {
        if ($entraClient -and $entraClient -ne $configuredClient) { throw 'The supplied Entra ID differs from the configured provider. Select a new web app to create a separate installation.' }
        if ($entraMode -eq 'Create') { throw 'This app already has an Entra provider. Select a new web app to create a new registration; the existing connection will not be replaced.' }
        Write-PortalStatus INFO "Configured organization Entra registration: $configuredClient (from App Service Authentication)."
        if (-not $entraMode) {
            $reuse = Read-PortalChoice 'Reuse configured Entra registration?' @(@{ Value='Yes'; Label='Yes, retain and validate the existing connection' }, @{ Value='No'; Label='No, create a separate web app and registration; preserve this app' }) 'Yes'
            if ($reuse -eq 'No') {
                Write-PortalStatus INFO 'The existing app/provider will remain unchanged. Choose a new web app name for the separate registration.'
                $newAppName = Get-PortalNewResourceName "app-$suffix" @($resources | Where-Object { $_.type -eq 'Microsoft.Web/sites' } | ForEach-Object { $_.name })
                $separate = @{} + $inputs
                $separate.AppName = Read-PortalChoice 'Separate web app' -AllowNew -NewDefault $newAppName
                if ($separate.AppName -in @($resources | Where-Object { $_.type -eq 'Microsoft.Web/sites' } | ForEach-Object { $_.name })) { throw 'Select a new web app name; the existing identity will not be replaced.' }
                $separate.EntraMode='Create'; $separate.EntraClientId=''; $separate.EntraDisplayName=''
                return Get-PortalDiscoveredInputs $separate
            }
            $entraMode = 'Existing'
        }
        $entraClient = $configuredClient
    } elseif (-not $entraMode -and -not $entraClient) {
        $storedClient = [string]$settings.PORTAL_ENTRA_CLIENT_ID
        $candidates = @()
        try {
            $candidates = @(Get-PortalTenantRegistrations $account.tenantId)
        } catch { Write-Warning 'Entra discovery was unavailable. You can supply an existing client ID or select Create; directory permissions will still be checked.' }
        if ($candidates.Count) {
            $options = @($candidates | ForEach-Object { @{ Value=$_.appId; Label="$($_.displayName) | $($_.appId) (tenant registration; validation required)" } })
            $options += @{ Value='Create'; Label='Create/resume a dedicated registration' }
            $options += @{ Value='Existing'; Label='Enter another existing client ID' }
            $related = @($candidates | Where-Object { $_.appId -eq $storedClient -or $_.displayName -eq $inputs.AppName -or
                ($site.properties.defaultHostName -and "https://$($site.properties.defaultHostName)/.auth/login/aad/callback" -in $_.web.redirectUris) })
            $choice = Read-PortalChoice 'Entra registration' $options $(if ($related.Count -eq 1) { $related[0].appId } else { 'Create' })
            if ($choice -in @('Create','Existing')) { $entraMode = $choice } else { $entraMode='Existing'; $entraClient=$choice }
        }
    }
    if (-not $entraMode) {
        $entraMode = if ($entraClient) { 'Existing' } else { Read-PortalChoice 'Entra registration' @('Create','Existing','Later' | ForEach-Object { @{ Value=$_; Label=$_ } }) 'Create' }
    }
    if ($entraMode -eq 'Existing' -and -not $entraClient) { $entraClient = Read-PortalFoundationValue 'Existing organization Entra client ID' '' }
    $displayName = $Values.EntraDisplayName
    if ($entraMode -eq 'Create' -and -not $displayName) {
        $suggestedDisplayName = if ($candidates.Count -eq 1 -and "DeploymentPortal:$appId" -in $candidates[0].tags) { $candidates[0].displayName } elseif ($candidates.Count) { "$($inputs.AppName)-portal" } else { $inputs.AppName }
        $displayName = Read-PortalFoundationValue 'New organization Entra registration display name' $suggestedDisplayName
    }
    return @{ Inputs=$inputs; EntraMode=$entraMode; EntraClientId=$entraClient; EntraDisplayName=$displayName; TenantId=$account.tenantId }
}

function Invoke-PortalFoundationGraph {
    param([string]$TenantId, [string]$Path, [ValidateSet('GET','POST','PATCH')][string]$Method = 'GET', [hashtable]$Body)
    $tenant = ConvertTo-PortalGuid $TenantId
    if ($Path -notmatch '^/(applications|servicePrincipals|users)(/[0-9a-f-]{36}(/(addPassword|appRoleAssignedTo))?)?(\?[^\r\n#]*)?$' -and
        ($Method -ne 'GET' -or $Path -ne '/me?$select=id,displayName,userType,accountEnabled')) { throw 'Unexpected registration API destination.' }
    if ($Method -eq 'PATCH' -and $Path -notmatch '^/(applications|servicePrincipals)/[0-9a-f-]{36}$') { throw 'Unexpected registration repair destination.' }
    $token = Invoke-PortalAzJson -Arguments @('account','get-access-token','--tenant',$tenant,'--resource-type','ms-graph') -Operation 'organization directory authorization'
    if ($token.tenant -ne $tenant) { throw 'Directory authorization returned a different tenant.' }
    $parameters = @{ Uri = "https://graph.microsoft.com/v1.0$Path"; Method = $Method; Headers = @{ Authorization = "Bearer $($token.accessToken)" }
        TimeoutSec = 60; MaximumRedirection = 0; ErrorAction = 'Stop' }
    if ($Body) { $parameters.ContentType = 'application/json'; $parameters.Body = ConvertTo-Json -InputObject $Body -Depth 30 -Compress }
    try {
        $response = Invoke-RestMethod @parameters
        return $response | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
    } catch {
        $failure = [InvalidOperationException]::new('Organization Entra operation failed. Check directory authority/policy and existing registrations before retrying. Remote content withheld; no credential or application was deleted.')
        if ($_.Exception.Response.StatusCode) { $failure.Data['HttpStatus'] = [int]$_.Exception.Response.StatusCode }
        throw $failure
    } finally { $token = $null; $parameters = $null; $response = $null }
}

function Get-PortalEntraReadiness {
    param([object]$Plan, [hashtable]$Snapshot, [string]$ClientId, [ValidateSet('Create','Existing','Later')][string]$Mode)
    if ($Mode -ne 'Existing') {
        return @{ Status=$(if ($Mode -eq 'Create') { 'PendingCreation' } else { 'Deferred' }); Checks=@([pscustomobject]@{
            Check='Entra'; Status='Pending'; Detail=$(if ($Mode -eq 'Create') { 'After hosting exists and you confirm, the script will create and configure the registration, store its credential securely, and verify the configuration.' } else { 'You chose Later. Configure Entra before deploying; this run will not connect an identity provider.' }) }) }
    }
    $checks = @()
    $stage = 'Selected host origin'
    $failureDetail = 'Configure an exact HTTPS portal origin bound to the selected App Service; remove any path, query or fragment from GALLERY_PUBLIC_URL.'
    try {
        $client = ConvertTo-PortalGuid $ClientId
        $origin = if ($Snapshot.Settings.properties.GALLERY_PUBLIC_URL) { [string]$Snapshot.Settings.properties.GALLERY_PUBLIC_URL } else { "https://$($Snapshot.Site.properties.defaultHostName)" }
        $uri = $null
        if (-not [Uri]::TryCreate($origin,[UriKind]::Absolute,[ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.Port -ne 443 -or
            $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or $origin.EndsWith('/') -or
            $uri.Host -notin $Snapshot.Site.properties.hostNames) {
            if (-not $Snapshot.Site) { return @{ Status='PendingHost'; Checks=@([pscustomobject]@{ Check='Entra callback'; Status='Pending'; Detail='After creating the host, the script will discover its exact hostname and check the callback. If missing, the script will offer to add it with separate approval.' }) } }
            throw 'Invalid host origin'
        }
        $filter = [Uri]::EscapeDataString("appId eq '$client'")
        $stage = 'Directory registration read'
        $failureDetail = 'Could not read the selected registration. Verify the client ID and tenant, and directory read access for the signed-in operator.'
        $found = Invoke-PortalFoundationGraph $Plan.TenantId "/applications?`$filter=$filter&`$select=id,appId,signInAudience,web,passwordCredentials,keyCredentials"
        $applications = @($found.value | Where-Object { $_.appId -eq $client })
        $failureDetail = 'The client ID did not resolve to one registration in the selected tenant. Choose the correct organization registration before continuing.'
        if ($found.'@odata.nextLink' -or $applications.Count -ne 1) { throw 'Registration cannot be resolved uniquely' }
        $application = $applications[0]
        $checks += [pscustomobject]@{ Check='Registration tenant'; Status=$(if ($application.signInAudience -eq 'AzureADMyOrg') { 'Ready' } else { 'Blocked' }); Detail='App registrations > Authentication: supported account types must be Accounts in this organizational directory only (AzureADMyOrg). The wizard does not change tenant mode.' }
        $callback = "$origin/.auth/login/aad/callback"
        $checks += [pscustomobject]@{ Check='Entra callback'; Status=$(if ($callback -cin $application.web.redirectUris) { 'Ready' } else { 'Repairable' }); Detail=$(if ($callback -cin $application.web.redirectUris) { "Web redirect configured: $callback" } else { "After approval, the script will add Web redirect: $callback. Existing redirect URLs will be kept." }) }
        $checks += [pscustomobject]@{ Check='Entra ID tokens'; Status=$(if ($application.web.implicitGrantSettings.enableIdTokenIssuance -eq $true) { 'Ready' } else { 'Repairable' }); Detail=$(if ($application.web.implicitGrantSettings.enableIdTokenIssuance -eq $true) { 'ID-token issuance is enabled for Easy Auth.' } else { 'After approval, the script will enable ID-token issuance for Easy Auth.' }) }
        $stage = 'Enabled enterprise application'
        $failureDetail = 'Could not read the enterprise application. Verify the signed-in operator has directory application read access in the selected tenant.'
        $foundPrincipals = Invoke-PortalFoundationGraph $Plan.TenantId "/servicePrincipals?`$filter=$filter&`$select=id,appId,appRoleAssignmentRequired,accountEnabled"
        $principals = @($foundPrincipals.value | Where-Object { $_.appId -eq $client })
        $failureDetail = 'The matching enterprise application is missing or could not be resolved uniquely. Ask the tenant administrator to provision/verify its service principal for this client ID.'
        if ($foundPrincipals.'@odata.nextLink' -or $principals.Count -ne 1) { throw 'An enterprise application is required' }
        $principal = $principals[0]
        $checks += [pscustomobject]@{ Check='Enterprise application enabled'; Status=$(if ($principal.accountEnabled -eq $true) { 'Ready' } else { 'Blocked' }); Detail='Enterprise applications > Properties: set Enabled for users to sign-in to Yes after administrator review.' }
        $principalId = ConvertTo-PortalGuid $principal.id
        $checks += [pscustomobject]@{ Check='Employee assignment policy'; Status=$(if ($principal.appRoleAssignmentRequired -eq $true) { 'Ready' } else { 'Repairable' }); Detail=$(if ($principal.appRoleAssignmentRequired -eq $true) { 'Employee assignment is required.' } else { 'After approval, the script will set Assignment required to Yes. Review other apps sharing this registration; unassigned users will lose access.' }) }
        $stage = 'Enterprise application assignments'
        $failureDetail = 'Could not verify enterprise-app user assignments. The signed-in operator needs permission to read app-role assignments; existing group memberships are not expanded by this check.'
        $assignments = Invoke-PortalFoundationGraph $Plan.TenantId "/servicePrincipals/$principalId/appRoleAssignedTo?`$select=principalId,appRoleId"
        $failureDetail = 'The enterprise-app assignment list is paginated. Review the complete assignments with a directory administrator; the wizard cannot certify a partial list.'
        if ($assignments.'@odata.nextLink') { throw 'Review paginated enterprise assignments manually' }
        $checks += [pscustomobject]@{ Check='Operator directory read access'; Status='Ready'; Detail='The current CLI identity successfully read the registration, enterprise application and app-role assignments. This does not verify repair-write authority.' }
        $missingAdmins = @(([string]$Plan.AdministratorIds).Split(',', [StringSplitOptions]::RemoveEmptyEntries) | Where-Object { $_ -notin $assignments.value.principalId })
        $checks += [pscustomobject]@{ Check='Initial administrator app access'; Status=$(if ($missingAdmins.Count) { 'Repairable' } else { 'Ready' }); Detail=$(if ($missingAdmins.Count) { "After approval, the script will add direct app access for designated enabled member administrators: $($missingAdmins -join ', '). Existing group assignments will be kept." } else { 'Designated administrators have direct enterprise-app assignments.' }) }
        $provider = $Snapshot.Authentication.properties.identityProviders.azureActiveDirectory
        if ($provider.registration.clientId) {
            $expectedIssuer = "https://login.microsoftonline.com/$($Plan.TenantId)/v2.0"
            $checks += [pscustomobject]@{ Check='Host Entra provider'; Status=$(if ($provider.registration.clientId -eq $client -and $provider.enabled -eq $true) { 'Ready' } else { 'Blocked' }); Detail="App Service > Authentication: the enabled Microsoft provider must use client ID $client. A different provider is never replaced automatically." }
            $checks += [pscustomobject]@{ Check='Host token issuer'; Status=$(if ($provider.registration.openIdIssuer -ceq $expectedIssuer) { 'Ready' } else { 'Blocked' }); Detail="App Service > Authentication: use tenant-specific issuer $expectedIssuer." }
            $checks += [pscustomobject]@{ Check='Host token audiences'; Status=$(if (@($provider.validation.allowedAudiences | Where-Object { $_ -ne $client }).Count) { 'Blocked' } else { 'Ready' }); Detail="App Service > Authentication: remove additional allowed token audiences; only the selected client ID $client is permitted." }
            $settingName = [string]$provider.registration.clientSecretSettingName
            $checks += [pscustomobject]@{ Check='Protected provider credential setting'; Status=$(if ($settingName -and $Snapshot.Settings.properties[$settingName]) { 'Ready' } else { 'Blocked' }); Detail='App Service > Authentication: reference an existing nonempty protected client-secret app setting or organization Key Vault reference. Configure the value securely, not in this prompt.' }
            $expiry = @(@($application.passwordCredentials) + @($application.keyCredentials) | Where-Object {
                $end = [DateTimeOffset]::MinValue
                $_ -and [DateTimeOffset]::TryParse([string]$_.endDateTime,[ref]$end) -and $end -gt [DateTimeOffset]::UtcNow
            })
            $checks += [pscustomobject]@{ Check='Unexpired registration credential metadata'; Status=$(if ($expiry.Count) { 'Ready' } else { 'Blocked' }); Detail='App registrations > Certificates & secrets: an unexpired credential is required. Follow organization rotation procedures and update the protected host setting to match; metadata cannot verify the secret value or Key Vault resolution.' }
        } else {
            $checks += [pscustomobject]@{ Check='Host Entra provider'; Status='Pending'; Detail='After confirmation, the script will connect this App Service to the selected registration, configure protected credential storage, and verify the connection. If no credential is stored on this host, you will be prompted for the existing secret through hidden input.' }
        }
        return @{ Status=$(if (@($checks | Where-Object { $_.Status -eq 'Blocked' }).Count) { 'Blocked' } elseif (@($checks | Where-Object { $_.Status -eq 'Repairable' }).Count) { 'RequiresRepair' } else { 'Ready' }); Checks=$checks
            TenantId=$Plan.TenantId; ClientId=$client; Origin=$origin; Callback=$callback; Application=$application; Principal=$principal; MissingAdmins=$missingAdmins }
    } catch {
        $httpStatus = $_.Exception.Data['HttpStatus']
        $detail = "$failureDetail Remote content withheld."
        if ($httpStatus -eq 401) { $detail = 'Directory request returned HTTP 401. The CLI token was rejected; this can happen after successful login because of Continuous Access Evaluation or Conditional Access. Refresh the affected CLI session and verify Graph access; persistent challenges require tenant-admin investigation. Registration permissions could not be verified.' }
        elseif ($httpStatus -eq 403) { $detail = "Directory request returned HTTP 403 during $stage. Operator read access was denied: the Graph client needs Application.Read.All (or an accepted higher permission) and the signed-in operator needs a supported directory role for this operation. Azure subscription Owner alone is insufficient. These permissions belong to the operator/CLI, not the portal registration." }
        $checks += [pscustomobject]@{ Check=$stage; Status='Blocked'; Detail=$detail }
        return @{ Status='Blocked'; Checks=$checks }
    }
}

function Write-PortalEntraReadiness {
    param([object]$Readiness)
    $checks = @($Readiness.Checks | Where-Object { $null -ne $_ })
    $findings = @($checks | Where-Object { $_.Status -ne 'Ready' })
    Write-PortalSection 'ENTRA | Configuration and access checks'
    Write-PortalStatus INFO 'Portal sign-in, operator directory permissions and host managed-identity Azure roles are separate.'
    if ($Readiness.Status -eq 'Ready' -and $checks.Count -gt 0 -and $findings.Count -eq 0) {
        Write-PortalStatus OK 'Entra registration correctly configured (read-only configuration and app-access checks passed).'
        Write-PortalStatus ACTION 'Actual credential validity, Conditional Access and employee sign-in still require a browser sign-in test.'
        Write-PortalConsentAction -TenantId $Readiness.TenantId -ClientId $Readiness.ClientId -Origin $Readiness.Origin
    } else {
        if ($Readiness.Status -eq 'Blocked' -or @($findings | Where-Object { $_.Status -eq 'Blocked' }).Count) {
            Write-PortalStatus BLOCKED 'Entra has issues requiring attention before the script can proceed. Planned steps are listed separately; no changes have been made by these checks.'
        } elseif ($Readiness.Status -eq 'Deferred') {
            Write-PortalStatus INFO 'Entra configuration is deferred by your selection.'
        } elseif ($findings.Count) {
            Write-PortalStatus INFO 'Entra script steps remaining (not failures): the script will perform these after the required approvals, then verify the result.'
        }
        foreach ($finding in $findings) {
            if ($finding.Status -eq 'Blocked') {
                Write-PortalStatus BLOCKED "$($finding.Check) [Needs attention]: $($finding.Detail)"
            } elseif ($Readiness.Status -eq 'Deferred') {
                Write-PortalStatus ACTION "$($finding.Check) [Deferred by choice]: $($finding.Detail)"
            } elseif ($finding.Status -in @('Repairable', 'Pending')) {
                $label = if ($finding.Status -eq 'Repairable') { 'Script repair after approval' } else { 'Script step after approval' }
                Write-PortalStatus PLAN "$($finding.Check) [$label]: $($finding.Detail)"
            } else {
                Write-PortalStatus WARN "$($finding.Check) [Not verified]: $($finding.Detail)"
            }
        }
        if (-not $findings.Count) { Write-PortalStatus WARN "Validation status: $($Readiness.Status); no complete set of passing checks is available." }
        $repairs = @($findings | Where-Object { $_.Status -eq 'Repairable' })
        if (@($repairs | Where-Object { $_.Check -in @('Entra callback','Entra ID tokens','Employee assignment policy') }).Count) {
            Write-PortalStatus INFO 'The script uses your existing directory application-write authority for these registration/policy repairs. Write access has not been tested; this is not a request to grant new permissions.'
        }
        if (@($repairs | Where-Object { $_.Check -eq 'Initial administrator app access' }).Count) {
            Write-PortalStatus INFO 'The listed administrator-assignment repair uses AppRoleAssignment.ReadWrite.All with the required directory role. Write access has not been tested; the script does not grant itself permissions.'
        }
        if (@($findings | Where-Object { $_.Check -eq 'Host Entra provider' -and $_.Status -eq 'Pending' }).Count) {
            Write-PortalStatus INFO 'Provider connection uses your existing App Service configuration-write access. Credentials are never copied from another host automatically.'
        }
        if ($findings.Count -and $Readiness.Status -ne 'Deferred') {
            Write-PortalStatus INFO 'Your next step is to review the proposed changes and approve the prompts. WhatIf only previews; no repair or creation runs without approval. Any denied write requires administrator attention.'
        }
    }
}

function Repair-PortalEntraReadiness {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param([object]$Plan, [hashtable]$Snapshot, [object]$Readiness)
    if ($Readiness.Status -eq 'Ready') { return $true }
    if ($Readiness.Status -ne 'RequiresRepair') { throw 'Entra findings are not safe for automatic repair. No directory settings were changed.' }
    Write-PortalSection 'ENTRA REPAIRS | Review before confirmation'
    $Readiness.Checks | Where-Object { $_.Status -eq 'Repairable' } | ForEach-Object { Write-PortalStatus PLAN "$($_.Check): $($_.Detail)" }
    if (-not $PSCmdlet.ShouldProcess($Readiness.ClientId, 'Append the exact host callback, enable ID tokens, require assignments and grant direct app access only to designated enabled member administrators; preserve other settings')) { return $false }
    $fresh = Get-PortalEntraReadiness $Plan $Snapshot $Readiness.ClientId Existing
    if ($fresh.Status -notin @('Ready','RequiresRepair') -or $fresh.Application.id -ne $Readiness.Application.id -or
        $fresh.Principal.id -ne $Readiness.Principal.id -or
        (ConvertTo-Json -InputObject $fresh.Application.web -Depth 30 -Compress) -cne (ConvertTo-Json -InputObject $Readiness.Application.web -Depth 30 -Compress)) {
        throw 'Entra configuration changed or is no longer repairable. Preview again before applying.'
    }
    $applicationId = ConvertTo-PortalGuid $fresh.Application.id
    $principalId = ConvertTo-PortalGuid $fresh.Principal.id
    foreach ($administrator in $fresh.MissingAdmins) {
        $administrator = ConvertTo-PortalGuid $administrator
        $employee = Invoke-PortalFoundationGraph $Plan.TenantId "/users/$administrator`?`$select=id,userType,accountEnabled"
        if ($employee.id -ne $administrator -or $employee.userType -ne 'Member' -or $employee.accountEnabled -ne $true) { throw 'Automatic assignment requires enabled organization member administrators.' }
    }
    if (@($fresh.Checks | Where-Object { $_.Check -in @('Entra callback','Entra ID tokens') -and $_.Status -eq 'Repairable' }).Count) {
        $web = if ($fresh.Application.web) { @{} + $fresh.Application.web } else { @{} }
        if ($fresh.Callback -cnotin $web.redirectUris) { $web.redirectUris = @($web.redirectUris | Where-Object { $_ }) + @($fresh.Callback) }
        if (-not $web.implicitGrantSettings) { $web.implicitGrantSettings = @{} }
        $web.implicitGrantSettings.enableIdTokenIssuance = $true
        $null = Invoke-PortalFoundationGraph $Plan.TenantId "/applications/$applicationId" -Method PATCH -Body @{ web=$web }
    }
    if ($fresh.Principal.appRoleAssignmentRequired -ne $true) {
        $null = Invoke-PortalFoundationGraph $Plan.TenantId "/servicePrincipals/$principalId" -Method PATCH -Body @{ appRoleAssignmentRequired=$true }
    }
    foreach ($administrator in $fresh.MissingAdmins) {
        $null = Invoke-PortalFoundationGraph $Plan.TenantId "/servicePrincipals/$principalId/appRoleAssignedTo" -Method POST -Body @{
            principalId=$administrator; resourceId=$principalId; appRoleId='00000000-0000-0000-0000-000000000000'
        }
    }
    $verified = Get-PortalEntraReadiness $Plan $Snapshot $Readiness.ClientId Existing
    if ($verified.Status -ne 'Ready') { throw 'Entra repair readback is not ready. Inspect the registration and rerun; no automatic rollback or credential reset is attempted.' }
    return $true
}

function New-PortalCustomerRegistration {
    [CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High')]
    param([object]$Plan, [string]$Origin, [string]$DisplayName, [ValidateRange(7,180)][int]$CredentialDays = 90)
    if ([string]::IsNullOrWhiteSpace($DisplayName) -or $DisplayName.Length -gt 120 -or $DisplayName -match '[\x00-\x1f\x7f]') { throw 'Provide a registration display name of 1-120 visible characters.' }
    $uri = $null
    if (-not [Uri]::TryCreate($Origin, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or
        $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/' -or -not $uri.IsDefaultPort) { throw 'Use the HTTPS origin of the verified organization host.' }
    if (-not $PSCmdlet.ShouldProcess("$($Plan.TenantId): $DisplayName", 'Create/reuse single-tenant Entra app requiring employee assignment; assign designated administrators, store expiring credential in host settings, and connect Easy Auth')) {
        return @{ Status = 'PreviewOnly' }
    }
    $settingsPath = "$($Plan.AppResourceId)/config/appsettings"
    $settings = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path "$settingsPath/list?api-version=2024-11-01" -Method POST
    $auth = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path "$($Plan.AppResourceId)/config/authsettingsV2?api-version=2024-11-01"
    $configured = $auth.properties.identityProviders.azureActiveDirectory.registration.clientId
    if ($configured) {
        if ($settings.properties.PORTAL_ENTRA_CLIENT_ID -ne $configured) { throw 'An existing Entra provider is already attached. Choose Existing instead of creating/replacing its registration.' }
        Test-PortalEntraRegistration ([pscustomobject]@{ TenantId = $Plan.TenantId; EntraClientId = $configured; Origin = $Origin })
        return @{ Status = 'RegistrationRetained'; ClientId = $configured }
    }
    if ($auth.properties.identityProviders.customOpenIdConnectProviders.Count -gt 0) { throw 'Existing custom providers will not be replaced.' }
    $marker = "DeploymentPortal:$($Plan.AppResourceId)"
    $filter = [Uri]::EscapeDataString("displayName eq '$($DisplayName.Replace("'", "''"))'")
    $found = Invoke-PortalFoundationGraph $Plan.TenantId "/applications?`$filter=$filter&`$select=id,appId,tags,web,signInAudience"
    if ($found.'@odata.nextLink') { throw 'Registration name is not unique enough; narrow the display name.' }
    $candidates = @($found.value | Where-Object { $null -ne $_ })
    if ($candidates.Count -gt 1 -or ($candidates.Count -eq 1 -and $marker -notin $candidates[0].tags)) { throw 'A same-name registration is not owned by this deployment. Choose a unique name or explicitly reuse its client ID.' }
    if ($candidates.Count -eq 1) { $application = $candidates[0] } else {
        if ($settings.properties.PORTAL_ENTRA_CLIENT_SECRET -or $settings.properties.PORTAL_ENTRA_CLIENT_ID) { throw 'Existing credential settings have no matching registration. Reconcile them before creating another application.' }
        $application = Invoke-PortalFoundationGraph $Plan.TenantId '/applications' -Method POST -Body @{
            displayName = $DisplayName; signInAudience = 'AzureADMyOrg'; tags = @($marker)
            web = @{ redirectUris = @("$Origin/.auth/login/aad/callback"); implicitGrantSettings = @{ enableIdTokenIssuance = $true; enableAccessTokenIssuance = $false } }
        }
    }
    $client = ConvertTo-PortalGuid $application.appId
    $objectId = ConvertTo-PortalGuid $application.id
    Test-PortalEntraRegistration ([pscustomobject]@{ TenantId = $Plan.TenantId; EntraClientId = $client; Origin = $Origin }) -RegistrationOnly
    $principals = Invoke-PortalFoundationGraph $Plan.TenantId "/servicePrincipals?`$filter=appId%20eq%20'$client'&`$select=id,appId,appRoleAssignmentRequired"
    $matches = @($principals.value | Where-Object { $null -ne $_ })
    if ($principals.'@odata.nextLink' -or $matches.Count -gt 1) { throw 'Enterprise application could not be resolved uniquely.' }
    if ($matches.Count -eq 0) {
        $principal = Invoke-PortalFoundationGraph $Plan.TenantId '/servicePrincipals' -Method POST -Body @{ appId = $client; appRoleAssignmentRequired = $true }
    } else { $principal = $matches[0] }
    if ($principal.appRoleAssignmentRequired -ne $true) { throw 'Enable Assignment required on the existing enterprise application before continuing; it will not be silently changed.' }
    $principalId = ConvertTo-PortalGuid $principal.id
    $assignmentsPath = "/servicePrincipals/$principalId/appRoleAssignedTo"
    $assignments = Invoke-PortalFoundationGraph $Plan.TenantId "$assignmentsPath`?`$select=principalId,appRoleId"
    if ($assignments.'@odata.nextLink') { throw 'Review existing enterprise application assignments before continuing; paginated assignments are not changed automatically.' }
    foreach ($administrator in ([string]$Plan.AdministratorIds).Split(',', [StringSplitOptions]::RemoveEmptyEntries)) {
        $administrator = ConvertTo-PortalGuid $administrator
        $employee = Invoke-PortalFoundationGraph $Plan.TenantId "/users/$administrator`?`$select=id,userType,accountEnabled"
        if ($employee.id -ne $administrator -or $employee.userType -ne 'Member' -or $employee.accountEnabled -ne $true) { throw 'Initial administrators must be enabled organization member users, not external guests or application IDs.' }
        if ($administrator -notin @($assignments.value | ForEach-Object { $_.principalId })) {
            $null = Invoke-PortalFoundationGraph $Plan.TenantId $assignmentsPath -Method POST -Body @{
                principalId = $administrator; resourceId = $principalId; appRoleId = '00000000-0000-0000-0000-000000000000'
            }
        }
    }
    $credential = $null
    try {
        if ($settings.properties.PORTAL_ENTRA_CLIENT_SECRET) {
            if ($settings.properties.PORTAL_ENTRA_CLIENT_ID -ne $client -or $settings.properties.PORTAL_ENTRA_APPLICATION_OBJECT_ID -ne $objectId) { throw 'Existing secret belongs to another or unknown registration; it will not be reused.' }
        } else {
            $freshSettings = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path "$settingsPath/list?api-version=2024-11-01" -Method POST
            if ((ConvertTo-Json -InputObject $freshSettings.properties -Depth 40 -Compress) -cne
                (ConvertTo-Json -InputObject $settings.properties -Depth 40 -Compress)) { throw 'Host settings changed during Entra preparation. Rerun after reconciling the concurrent change.' }
            $settings = $freshSettings
            $expiry = [DateTimeOffset]::UtcNow.AddDays($CredentialDays).ToString('o')
            $credential = Invoke-PortalFoundationGraph $Plan.TenantId "/applications/$objectId/addPassword" -Method POST -Body @{
                passwordCredential = @{ displayName = 'Deployment Portal host'; endDateTime = $expiry }
            }
            if (-not $credential.secretText) { throw 'Entra did not return a credential value.' }
            $settings.properties['PORTAL_ENTRA_CLIENT_SECRET'] = $credential.secretText
            $settings.properties['PORTAL_ENTRA_CLIENT_ID'] = $client
            $settings.properties['PORTAL_ENTRA_APPLICATION_OBJECT_ID'] = $objectId
            $null = Invoke-PortalArm -Subscription $Plan.SubscriptionId -Path "$settingsPath`?api-version=2024-11-01" -Method PUT -Body @{ properties = $settings.properties } -ETag $settings.etag
            Write-PortalStatus ACTION "Entra client ID: $client. Credential expires $expiry; schedule rotation. No secret was printed or saved locally."
        }
    } finally { $credential = $null; $settings = $null; $freshSettings = $null }
    Connect-PortalPrerequisiteEntra -Plan $Plan -Origin $Origin -ClientId $client -NonInteractive -Confirm:$false
    return @{ Status = 'RegistrationConfigured'; ClientId = $client; ApplicationObjectId = $objectId }
}
