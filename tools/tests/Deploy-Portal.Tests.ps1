BeforeAll {
    . "$PSScriptRoot/../Deploy-Portal.ps1"
    $subscription = '11111111-1111-4111-8111-111111111111'
    $tenant = '22222222-2222-4222-8222-222222222222'
    $client = '33333333-3333-4333-8333-333333333333'
    $admin = '44444444-4444-4444-8444-444444444444'
    $target = "/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Web/sites/portal"
    $digest = 'sha256:' + ('a' * 64)
}

Describe 'Portal console output' {
    BeforeEach {
        Mock Write-Host {}
        Mock Write-Warning {}
    }

    It 'Separates sections without writing objects to the success pipeline' {
        @(Write-PortalSection 'HOSTING').Count | Should -Be 0
        Should -Invoke Write-Host -Times 4 -Exactly
        Should -Invoke Write-Host -Times 2 -Exactly -ParameterFilter { $Object -eq ('-' * 64) -and $ForegroundColor -eq 'Cyan' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq 'HOSTING' -and $ForegroundColor -eq 'Cyan' }
    }

    It 'Uses readable labels and color for <Status>' -ForEach @(
        @{Status='OK'; Color='Green'}, @{Status='INFO'; Color='Gray'},
        @{Status='PLAN'; Color='Cyan'},
        @{Status='BLOCKED'; Color='Red'}, @{Status='ACTION'; Color='Magenta'}
    ) {
        @(Write-PortalStatus $Status 'Example').Count | Should -Be 0
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq "[$Status] Example" -and $ForegroundColor -eq $Color }
    }

    It 'Preserves warning-stream behavior with a text label' {
        Write-PortalStatus WARN 'Needs review'
        Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter { $Message -eq '[WARN] Needs review' }
    }

    It 'Gives consent instructions without claiming approval or starting sign-in' {
        Mock Invoke-PortalAzJson { throw 'Unexpected Azure operation' }
        Mock Invoke-WebRequest { throw 'Unexpected browser operation' }
        Mock Start-Process { throw 'Unexpected browser launch' }
        Write-PortalConsentAction $tenant $client 'https://portal.azurewebsites.net'
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq "[ACTION] Administrator consent page: https://login.microsoftonline.com/$tenant/v2.0/adminconsent?client_id=$client&scope=openid%20profile%20email&redirect_uri=https%3A%2F%2Fportal.azurewebsites.net%2F.auth%2Flogin%2Faad%2Fcallback" }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq '[ACTION] 2. Portal sign-in (after consent and application startup): https://portal.azurewebsites.net' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Consent on behalf of your organization*' -and $ForegroundColor -eq 'Magenta' }
        Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter { $Message -like '*NOT VERIFIED*Assigning users does not grant consent*' }
        Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter { $Message -like '*Need admin approval even when deployment succeeds*' }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
        Should -Invoke Invoke-WebRequest -Times 0 -Exactly
        Should -Invoke Start-Process -Times 0 -Exactly
    }

    It 'Normalizes tenant and client IDs in the consent link' {
        Write-PortalConsentAction 'AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA' 'BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB' 'https://portal.azurewebsites.net'
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq '[ACTION] Administrator consent page: https://login.microsoftonline.com/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/v2.0/adminconsent?client_id=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb&scope=openid%20profile%20email&redirect_uri=https%3A%2F%2Fportal.azurewebsites.net%2F.auth%2Flogin%2Faad%2Fcallback' }
    }

    It 'Uses the selected host in the encoded consent callback: <_>' -ForEach @(
        'https://portal-first.azurewebsites.net', 'https://portal-second.azurewebsites.net/', 'https://portal.example.com:8443'
    ) {
        $selectedOrigin = $_.TrimEnd('/')
        $expectedCallback = "$selectedOrigin/.auth/login/aad/callback"
        Write-PortalConsentAction $tenant $client $_
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter {
            if (([string]$Object).StartsWith('[ACTION] Administrator consent page: ')) {
                $link = [uri]([string]$Object).Substring('[ACTION] Administrator consent page: '.Length)
                $query = [System.Web.HttpUtility]::ParseQueryString($link.Query)
                $link.Host -eq 'login.microsoftonline.com' -and $link.AbsolutePath -eq "/$tenant/v2.0/adminconsent" -and
                    $query.Count -eq 3 -and $query['scope'] -ceq 'openid profile email' -and
                    $query['client_id'] -eq $client -and $query['redirect_uri'] -ceq $expectedCallback
            } else { $false }
        }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq "[INFO] Consent return callback for the selected host: $expectedCallback" }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq "[ACTION] 2. Portal sign-in (after consent and application startup): $selectedOrigin" }
    }

    It 'Does not let Entra choose a default callback when the host is unknown' {
        Write-PortalConsentAction $tenant $client
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -like '*adminconsent?*' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*consent link is deferred*' }
    }

    It 'Requests basic sign-in consent without depending on static API permissions or granting access' {
        Mock Invoke-PortalAzJson { throw 'Unexpected directory write or permission lookup' }
        Mock Invoke-RestMethod { throw 'Unexpected consent request' }
        Mock Start-Process { throw 'Unexpected browser launch' }
        Write-PortalConsentAction $tenant $client 'https://portal.azurewebsites.net'
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*v2.0/adminconsent?*scope=openid%20profile%20email*' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*v2 consent link works with an empty configured API-permissions list*' }
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -match '[?&]scope=.*(\.default|User\.Read|Directory\.|Application\.)' }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
        Should -Invoke Invoke-RestMethod -Times 0 -Exactly
        Should -Invoke Start-Process -Times 0 -Exactly
    }

    It 'Rejects a consent origin with unsafe or ambiguous components: <_>' -ForEach @(
        'http://portal.example.com', 'https://user@portal.example.com', 'https://portal.example.com/path',
        'https://portal.example.com?redirect_uri=https://other.example.com', 'https://portal.example.com#fragment', '/relative'
    ) {
        { Write-PortalConsentAction $tenant $client $_ } | Should -Throw '*HTTPS origin*'
        Should -Invoke Write-Host -Times 0 -Exactly
    }

    It 'Does not guess a consent tenant before registration is known' {
        Write-PortalConsentAction -ClientId $client -Origin 'https://portal.azurewebsites.net'
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -like '*adminconsent?*' }
    }

    It 'Rejects invalid consent identifiers' -ForEach @('organizations', '00000000-0000-0000-0000-000000000000', 'bad-id&redirect_uri=https://untrusted.example') {
        { Write-PortalConsentAction -TenantId $_ -ClientId $client } | Should -Throw
        { Write-PortalConsentAction -TenantId $tenant -ClientId $_ } | Should -Throw
        Should -Invoke Write-Host -Times 0 -Exactly
    }
}

Describe 'Always On readiness' {
    It 'Reports <_> as advisory without requiring a paid upgrade' -ForEach @('F1','D1') {
        $result = Get-PortalHostingReadiness @{ sku=@{ name=$_ } } @{ properties=@{ alwaysOn=$false } }
        $result.Status | Should -Be 'Warning'
        $result.UpgradeRequired | Should -BeFalse
        $result.TargetSku | Should -Be $_
        $result.Detail | Should -Match 'may continue'
    }

    It 'Still rejects unrecognized hosting tiers' {
        (Get-PortalHostingReadiness @{ sku=@{ name='unknown' } } @{}).Status | Should -Be 'Blocked'
    }

    It 'Plans an explicitly selected paid upgrade and enabling Always On' {
        $result = Get-PortalHostingReadiness @{ sku=@{ name='F1' } } @{ properties=@{ alwaysOn=$false } } -UpgradePlanSku B1
        $result.Status | Should -Be 'ChangePlanned'
        $result.UpgradeRequired | Should -BeTrue
        $result.TargetSku | Should -Be 'B1'
    }

    It 'Retains paid tiers and reports whether Always On needs enabling' -ForEach @($true,$false) {
        $result = Get-PortalHostingReadiness @{ sku=@{ name='P1v3' } } @{ properties=@{ alwaysOn=$_ } }
        $result.Status | Should -Be $(if ($_){'Ready'}else{'ChangePlanned'})
        $result.UpgradeRequired | Should -BeFalse
    }

    It 'Never interprets a new-plan SKU as approval to resize an existing plan' {
        (Get-PortalHostingReadiness @{ sku=@{ name='F1' } } @{} -NewPlanSku B1).Status | Should -Be 'Warning'
        { Get-PortalHostingReadiness @{ sku=@{ name='P1v3' } } @{} -UpgradePlanSku B1 } | Should -Throw '*not resized*'
    }
}

Describe 'Post-deployment health observation' {
    It 'Checks application health without claiming employee sign-in' {
        Mock Invoke-WebRequest { return @{ StatusCode=200; Content='{"status":"ok"}' } }
        (Test-PortalDeploymentHealth 'https://portal.azurewebsites.net').Status | Should -Be 'HealthyResponse'
        Should -Invoke Invoke-WebRequest -Times 1 -Exactly -ParameterFilter { $MaximumRedirection -eq 0 -and $TimeoutSec -eq 30 -and $Uri -eq 'https://portal.azurewebsites.net/healthz' }
    }

    It 'Does not follow login redirects or bypass authentication' -ForEach @(302,401,403) {
        $httpStatus = $_
        Mock Invoke-WebRequest { return @{ StatusCode=$httpStatus; Content='login required' } }
        (Test-PortalDeploymentHealth 'https://portal.azurewebsites.net').Status | Should -Be 'SignInRequired'
        Should -Invoke Invoke-WebRequest -Times 1 -Exactly
    }

    It 'Reports startup errors and non-health HTML without treating them as success' -ForEach @(503,200) {
        $httpStatus = $_
        Mock Invoke-WebRequest { return @{ StatusCode=$httpStatus; Content='<html>starting</html>' } }
        (Test-PortalDeploymentHealth 'https://portal.azurewebsites.net').Status | Should -BeIn @('PendingOrFailed','Unverified')
    }
}

Describe 'Organization deployment planning' {
    BeforeEach {
        Mock Invoke-PortalAzJson { return @{} }
        Mock Test-PortalDeploymentHealth { return @{ Status='SignInRequired' } }
        $snapshot = @{
            Subscription = @{ id = $subscription; tenantId = $tenant }
            Site = @{
                id = $target; kind = 'app,linux'
                properties = @{
                    httpsOnly = $true; publicNetworkAccess = 'Disabled'; defaultHostName = 'portal.azurewebsites.net'
                    hostNames = @('portal.azurewebsites.net'); virtualNetworkSubnetId = '/example/subnet'; vnetImagePullEnabled = $true
                    privateEndpointConnections = @(@{ properties = @{ privateLinkServiceConnectionState = @{ status = 'Approved' } } })
                }
                identity = @{ type = 'SystemAssigned'; principalId = $admin }
            }
            Plan = @{ sku=@{ name='B1' }; properties = @{ numberOfWorkers = 1; elasticScaleEnabled = $false } }
            Web = @{ properties = @{ linuxFxVersion = 'PYTHON|3.12'; appCommandLine = 'old-startup'; alwaysOn = $true } }
            Settings = @{ properties = @{ UNRELATED_SECRET = 'synthetic-secret'; GALLERY_DB_PATH = '/home/data/existing.db' } }
            Authentication = @{ properties = @{
                platform = @{ enabled = $true }; httpSettings = @{ requireHttps = $true }
                globalValidation = @{ requireAuthentication = $true; excludedPaths = @(); unauthenticatedClientAction = 'RedirectToLoginPage'; redirectToProvider = 'azureActiveDirectory' }
                identityProviders = @{ azureActiveDirectory = @{
                    enabled = $true; registration = @{ clientId = $client; openIdIssuer = "https://login.microsoftonline.com/$tenant/v2.0" }
                    validation = @{ allowedAudiences = @($client) }
                } }
            } }
            Registry = @{ id = "/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.ContainerRegistry/registries/companyregistry"; loginServer = 'companyregistry.azurecr.io'; publicNetworkAccess = 'Disabled'; adminUserEnabled = $false }
            Manifest = @{ digest = $digest }
        }
    }

    It 'Produces a digest-pinned, non-secret plan without modifying its input' {
        $before = $snapshot | ConvertTo-Json -Depth 30
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        $plan.Image | Should -Be "companyregistry.azurecr.io/deployment-portal@$digest"
        ($plan | ConvertTo-Json) | Should -Not -Match 'synthetic-secret'
        ($snapshot | ConvertTo-Json -Depth 30) | Should -Be $before
    }

    It 'Exposes script help and a read-only example' {
        $tokens = $null
        $parseErrors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile("$PSScriptRoot/../Deploy-Portal.ps1", [ref]$tokens, [ref]$parseErrors)
        $parseErrors | Should -HaveCount 0
        $ast.GetHelpContent().Synopsis | Should -Match 'Interactively prepares organization hosting or deploys'
        ($ast.GetHelpContent().Examples -join ' ') | Should -Match 'WhatIf'
    }

    It 'Rejects <_> on F1 before writes' -ForEach @('http', 'anonymous', 'excluded-path', 'wrong-tenant', 'extra-audience', 'sidecar', 'scale-out', 'installer', 'registry-admin', 'bad-digest', 'wrong-subscription') {
        $snapshot.Plan.sku.name = 'F1'
        switch ($_) {
            'http' { $snapshot.Site.properties.httpsOnly = $false }
            'anonymous' { $snapshot.Authentication.properties.globalValidation.requireAuthentication = $false }
            'excluded-path' { $snapshot.Authentication.properties.globalValidation.excludedPaths = @('/install') }
            'wrong-tenant' { $snapshot.Authentication.properties.identityProviders.azureActiveDirectory.registration.openIdIssuer = 'https://login.microsoftonline.com/common/v2.0' }
            'extra-audience' { $snapshot.Authentication.properties.identityProviders.azureActiveDirectory.validation.allowedAudiences += $admin }
            'sidecar' { $snapshot.Web.properties.linuxFxVersion = 'sitecontainers' }
            'scale-out' { $snapshot.Plan.properties.numberOfWorkers = 2 }
            'installer' { $snapshot.Settings.properties.GALLERY_INSTALLER_ENABLED = 'true' }
            'registry-admin' { $snapshot.Registry.adminUserEnabled = $true }
            'bad-digest' { $snapshot.Manifest.digest = 'untrusted' }
            'wrong-subscription' { $snapshot.Subscription.id = $admin }
        }
        { Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin) } | Should -Throw
    }

    It 'Accepts organization networking choices without requiring private connectivity' -ForEach @('Enabled','Disabled') {
        $snapshot.Site.properties.publicNetworkAccess = $_
        $snapshot.Registry.publicNetworkAccess = $_
        $snapshot.Site.properties.privateEndpointConnections = @()
        $snapshot.Site.properties.virtualNetworkSubnetId = ''
        $snapshot.Site.properties.vnetImagePullEnabled = $false
        $before = $snapshot | ConvertTo-Json -Depth 30
        (Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)).EntraClientId | Should -Be $client
        ($snapshot | ConvertTo-Json -Depth 30) | Should -Be $before
    }

    It 'Does not silently replace existing administrator eligibility' {
        $snapshot.Settings.properties.GALLERY_ADMIN_OIDS = $client
        { Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin) } | Should -Throw '*administrator*'
    }

    It 'Preserves an explicitly selected attached user-assigned identity' {
        $snapshot.Settings.properties.AZURE_CLIENT_ID = $client
        $snapshot.Site.identity = @{ type = 'UserAssigned'; userAssignedIdentities = @{
            '/example/identity' = @{ clientId = $client; principalId = $admin }
        } }
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        $plan.IdentityMode | Should -Be 'user'
        $plan.IdentityClientId | Should -Be $client
    }

    It 'Rejects conflicting app resource overrides' {
        $snapshot.Settings.properties.GALLERY_HOST_RESOURCE_ID = "$target-other"
        { Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin) } | Should -Throw '*another app*'
    }

    It 'Rejects image references that could be options or commands' -ForEach @('--help', 'repo;command:tag', 'registry.example/repo', '../repo:tag', 'repo@sha256:bad') {
        $reference = $_
        { Get-PortalDeploymentPlan $snapshot $target $reference @($admin) } | Should -Throw
    }

    It 'Never sends Azure writes for WhatIf on <_>' -ForEach @('B1','F1') {
        $snapshot.Plan.sku.name = $_
        $snapshot.Web.properties.alwaysOn = $false
        Mock Invoke-PortalArm { throw 'Unexpected Azure request' }
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        $result = Set-PortalDeployment -Plan $plan -Snapshot $snapshot -Subscription $subscription -WhatIf
        $result.Status | Should -Be 'PreviewOnly'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
        Should -Invoke Test-PortalDeploymentHealth -Times 0 -Exactly
    }

    It 'Deploys on <_> without resizing the plan or changing auth and data settings' -ForEach @('B1','F1') {
        $hostingSku = $_
        $snapshot.Plan.sku.name = $hostingSku
        $snapshot.Web.properties.alwaysOn = $false
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        $script:state = $snapshot | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
        Mock Invoke-PortalArm {
            $key = if ($Path -like '*/config/authsettingsV2?*') { 'Authentication' }
                elseif ($Path -like '*/config/appsettings*') { 'Settings' }
                elseif ($Path -like '*/config/web?*') { 'Web' } else { 'Site' }
            if ($Method -eq 'PUT') { $script:state[$key] = $Body }
            return $script:state[$key] | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
        }
        $result = Set-PortalDeployment -Plan $plan -Snapshot $snapshot -Subscription $subscription -Confirm:$false
        $result.Status | Should -Be 'ConfigurationApplied'
        $result.RuntimeVerified | Should -BeFalse
        $script:state.Settings.properties.UNRELATED_SECRET | Should -Be 'synthetic-secret'
        $script:state.Settings.properties.GALLERY_DB_PATH | Should -Be '/home/data/existing.db'
        $script:state.Settings.properties.DOCKER_REGISTRY_SERVER_URL | Should -Be 'https://companyregistry.azurecr.io'
        $script:state.Web.properties.alwaysOn | Should -Be ($hostingSku -eq 'B1')
        $script:state.Plan.sku.name | Should -Be $hostingSku
        $plan.HostingReadiness.UpgradeRequired | Should -BeFalse
        $script:state.Web.properties.appCommandLine | Should -Be ''
        $result.HealthObservation | Should -Be 'SignInRequired'
        Should -Invoke Test-PortalDeploymentHealth -Times 1 -Exactly
        Should -Invoke Invoke-PortalArm -Times 2 -Exactly -ParameterFilter { $Method -eq 'PUT' }
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PATCH' -or $Path -like '*/serverfarms/*' }
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -like '*/authsettingsV2?*' }
    }

    It 'Rejects concurrent configuration changes without writing' {
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        Mock Invoke-PortalArm { return @{ properties = @{ changed = $true } } }
        { Set-PortalDeployment -Plan $plan -Snapshot $snapshot -Subscription $subscription -Confirm:$false } | Should -Throw '*changed after*'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' }
    }

    It 'Stops on a failed write without resetting auth or exposing remote errors' {
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        $script:state = $snapshot | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
        Mock Invoke-PortalArm {
            if ($Method -eq 'PUT') { throw 'synthetic-sensitive-remote-error' }
            $key = if ($Path -like '*/config/authsettingsV2?*') { 'Authentication' }
                elseif ($Path -like '*/config/appsettings*') { 'Settings' }
                elseif ($Path -like '*/config/web?*') { 'Web' } else { 'Site' }
            return $script:state[$key] | ConvertTo-Json -Depth 30 | ConvertFrom-Json -AsHashtable
        }
        { Set-PortalDeployment -Plan $plan -Snapshot $snapshot -Subscription $subscription -Confirm:$false } | Should -Throw '*One settings write may have succeeded*'
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' }
    }

    It 'Verifies Entra ID-token issuance rather than repairing the registration' {
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        Mock Invoke-PortalAzJson { return @{ tenant = $tenant; accessToken = 'synthetic-token' } }
        Mock Invoke-RestMethod { return @{ appId = $client; signInAudience = 'AzureADMyOrg'; web = @{ redirectUris = @('https://portal.azurewebsites.net/.auth/login/aad/callback'); implicitGrantSettings = @{ enableIdTokenIssuance = $false } } } }
        { Test-PortalEntraRegistration -Plan $plan } | Should -Throw '*ID-token issuance*'
        Should -Invoke Invoke-RestMethod -Times 1 -Exactly
    }

    It 'Requires explicit enterprise application assignments' -ForEach @($true,$false) {
        $required = $_
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        Mock Invoke-PortalAzJson { return @{ tenant = $tenant; accessToken = 'synthetic-token' } }
        Mock Invoke-RestMethod {
            if ($Uri -like '*/servicePrincipals?*') { return @{ value = @(@{ id=$admin; appId=$client; appRoleAssignmentRequired=$required }) } }
            return @{ appId=$client; signInAudience='AzureADMyOrg'; web=@{ redirectUris=@('https://portal.azurewebsites.net/.auth/login/aad/callback'); implicitGrantSettings=@{ enableIdTokenIssuance=$true } } }
        }
        if ($required) { Test-PortalEntraRegistration $plan }
        else { { Test-PortalEntraRegistration $plan } | Should -Throw '*Assignment required*' }
        Should -Invoke Invoke-RestMethod -Times 2 -Exactly
    }

    It 'Rejects a redirected or unscoped ARM destination before obtaining credentials' {
        Mock Invoke-PortalAzJson { throw 'Unexpected token request' }
        { Invoke-PortalArm -Subscription $subscription -Path 'https://untrusted.example/' } | Should -Throw '*destination*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Runs full WhatIf with prompts but no writes and no secret output' {
        Mock Get-PortalSnapshot { return $snapshot }
        Mock Test-PortalEntraRegistration {}
        Mock Invoke-PortalArm { throw 'Unexpected Azure write' }
        $script:answers = [System.Collections.Generic.Queue[string]]::new()
        foreach ($answer in @($subscription, 'test', 'portal', 'companyregistry', 'deployment-portal:release-1', $admin)) { $script:answers.Enqueue($answer) }
        Mock Read-Host { return $script:answers.Dequeue() }
        $result = Invoke-PortalDeployment -WhatIf
        $result.Status | Should -Be 'PreviewOnly'
        Should -Invoke Read-Host -Times 6 -Exactly
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
    }

    It 'Requires explicit confirmation policy for noninteractive apply' {
        Mock Get-PortalSnapshot { return $snapshot }
        Mock Test-PortalEntraRegistration {}
        Mock Set-PortalDeployment { throw 'Unexpected apply' }
        { Invoke-PortalDeployment -SubscriptionId $subscription -ResourceGroupName test -AppName portal -RegistryName companyregistry -Image 'deployment-portal:release-1' -AdminObjectIds $admin -NonInteractive } | Should -Throw '*Confirm*'
        Should -Invoke Set-PortalDeployment -Times 0 -Exactly
    }

    It 'Warns about consent after Entra validation and before deployment in <_> mode' -ForEach @('Preview','Interactive','Unattended') {
        $script:consentStages = [System.Collections.Generic.List[string]]::new()
        Mock Get-PortalSnapshot { return $snapshot }
        Mock Test-PortalEntraRegistration { $script:consentStages.Add('Entra validated') }
        Mock Write-PortalConsentAction { $script:consentStages.Add('Consent warning') }
        Mock Set-PortalDeployment {
            $script:consentStages.Add('Deployment entry')
            return @{ Status='PreviewOnly' }
        }
        Mock Read-Host { throw 'Unexpected interactive prompt' }
        $options = @{ SubscriptionId=$subscription; ResourceGroupName='test'; AppName='portal'; RegistryName='companyregistry'; Image='deployment-portal:release-1'; AdminObjectIds=@($admin) }
        if ($_ -eq 'Preview') { $options.WhatIf = $true }
        else { $options.Confirm = $false }
        if ($_ -eq 'Unattended') { $options.NonInteractive = $true }
        $null = Invoke-PortalDeployment @options
        ($script:consentStages -join ',') | Should -Be 'Entra validated,Consent warning,Deployment entry'
        Should -Invoke Write-PortalConsentAction -Times 1 -Exactly -ParameterFilter { $TenantId -eq $tenant -and $ClientId -eq $client -and $Origin -eq 'https://portal.azurewebsites.net' }
        Should -Invoke Read-Host -Times 0 -Exactly
    }

    It 'Separates configuration success from <Observation> health and consent status' -ForEach @(
        @{Observation='HealthyResponse'; Label='OK'}, @{Observation='SignInRequired'; Label='WARN'},
        @{Observation='Unverified'; Label='WARN'}, @{Observation='PendingOrFailed'; Label='WARN'}
    ) {
        $healthObservation=$Observation
        $healthLabel=$Label
        Mock Get-PortalSnapshot { return $snapshot }
        Mock Test-PortalEntraRegistration {}
        Mock Set-PortalDeployment { return [pscustomobject]@{ Status='ConfigurationApplied'; HealthObservation=$healthObservation; HealthDetail='Check detail'; RuntimeVerified=$false; SignInVerified=$false } }
        Mock Write-PortalStatus {}
        Mock Write-PortalSection {}
        Mock Read-Host { throw 'Unexpected consent/sign-in interaction' }
        $result=Invoke-PortalDeployment -SubscriptionId $subscription -ResourceGroupName test -AppName portal -RegistryName companyregistry -Image 'deployment-portal:release-1' -AdminObjectIds $admin -NonInteractive -Confirm:$false
        $result.RuntimeVerified | Should -BeFalse
        $result.SignInVerified | Should -BeFalse
        $result.HealthDetail | Should -Be 'Check detail'
        Should -Invoke Write-PortalSection -Times 1 -Exactly -ParameterFilter { $Title -eq 'DEPLOYMENT RESULT | Configuration applied, verification pending' }
        Should -Invoke Write-PortalStatus -Times 1 -Exactly -ParameterFilter { $Status -eq $healthLabel -and $Message -eq "Health observation: $healthObservation. Check detail" }
        Should -Invoke Write-PortalStatus -Times 1 -Exactly -ParameterFilter { $Status -eq 'WARN' -and $Message -like '*employee sign-in are NOT VERIFIED*' }
        Should -Invoke Write-PortalStatus -Times 2 -Exactly -ParameterFilter { $Status -eq 'ACTION' -and $Message -like '*Consent on behalf of your organization*' }
        Should -Invoke Read-Host -Times 0 -Exactly
    }
}