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

Describe 'Read-only check progress' {
    BeforeEach {
        $messages = [System.Collections.Generic.List[string]]::new()
        Mock Write-PortalStatus { $messages.Add("${Status}|$Message") }
        Mock Write-Progress {}
    }

    It 'Announces the operation before invoking it and returns only its data' {
        $result = @(Invoke-PortalReadCheck 'Protected settings' -ReportProgress -Action {
            $messages.Count | Should -Be 1
            $messages[0] | Should -Match '^INFO\|Protected settings - starting; waiting'
            return @{ secret='synthetic-sensitive-value' }
        })
        $result.Count | Should -Be 1
        $result[0].secret | Should -Be 'synthetic-sensitive-value'
        $messages[1] | Should -Match '^OK\|Protected settings - completed in .+s\.$'
        ($messages -join ' ') | Should -Not -Match 'synthetic-sensitive-value'
        Should -Invoke Write-Progress -Times 1 -Exactly -ParameterFilter { $Id -eq 42 -and $PercentComplete -eq -1 -and -not $Completed }
        Should -Invoke Write-Progress -Times 1 -Exactly -ParameterFilter { $Id -eq 42 -and $Completed }
    }

    It 'Reports a failed check without echoing the response and clears progress' {
        { Invoke-PortalReadCheck 'Registry lookup' -ReportProgress -Action { throw 'synthetic-sensitive-value' } } | Should -Throw 'synthetic-sensitive-value'
        $messages.Count | Should -Be 2
        $messages[1] | Should -Match '^BLOCKED\|Registry lookup - failed after .+s\.'
        ($messages -join ' ') | Should -Not -Match 'synthetic-sensitive-value|completed in'
        Should -Invoke Write-Progress -Times 1 -Exactly -ParameterFilter { $Completed }
    }

    It 'Keeps non-progress callers quiet and preserves array results' {
        $result = @(Invoke-PortalReadCheck 'Existing caller' -Action { 'first'; 'second' })
        $result | Should -HaveCount 2
        $result[0] | Should -Be 'first'
        Should -Invoke Write-PortalStatus -Times 0 -Exactly
        Should -Invoke Write-Progress -Times 0 -Exactly
    }

    It 'Announces each snapshot read before the read and never prints setting values' {
        $planId = "/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Web/serverfarms/plan"
        Mock Invoke-PortalAzJson {
            $messages[$messages.Count - 1] | Should -Match '^INFO\|.+starting; waiting'
            if ($Arguments[0] -eq 'account') { return @{ id=$subscription; environmentName='AzureCloud'; state='Enabled' } }
            return @{ digest=$digest }
        }
        Mock Invoke-PortalArm {
            $messages[$messages.Count - 1] | Should -Match '^INFO\|.+starting; waiting'
            if ($Path -eq "$target`?api-version=2024-11-01") { return @{ properties=@{ serverFarmId=$planId } } }
            return @{ properties=@{ secret='synthetic-sensitive-value' } }
        }
        $result = Get-PortalSnapshot -Subscription $subscription -AppResourceId $target -Registry companyregistry -ImageReference 'portal:release' -ReportProgress
        $result.Settings.properties.secret | Should -Be 'synthetic-sensitive-value'
        @($messages | Where-Object { $_ -like 'INFO|*' }) | Should -HaveCount 8
        @($messages | Where-Object { $_ -like 'OK|*' }) | Should -HaveCount 8
        ($messages -join ' ') | Should -Not -Match 'synthetic-sensitive-value'
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

Describe 'Local image publication' {
    BeforeAll {
        function docker { param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments) throw 'Unmocked Docker call' }
        function az { param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments) throw 'Unmocked Azure call' }
    }
    BeforeEach {
        $previousExitCode = $global:LASTEXITCODE
        $commands = [System.Collections.Generic.List[object]]::new()
        $failure = ''
        $published = 'sha256:' + ('b' * 64)
        $buildPlan = @{ SubscriptionId=$subscription; Inputs=@{ RegistryName='companyregistry' } }
        Mock Write-Host {}
        Mock Write-Warning {}
        Mock az { $global:LASTEXITCODE = if ($failure -eq 'login') { 1 } else { 0 } }
        Mock docker {
            param($Arguments)
            $commands.Add(@($Arguments))
            $global:LASTEXITCODE = if ($failure -eq $Arguments[0]) { 1 } else { 0 }
            if ($Arguments[0] -eq 'image') {
                if ($failure -eq 'digest') { return '["other.azurecr.io/portal@sha256:bad"]' }
                return "[`"companyregistry.azurecr.io/deployment-portal@$published`"]"
            }
        }
    }
    AfterEach { $global:LASTEXITCODE = $previousExitCode }

    It 'Builds local linux/amd64 source, pushes, and returns the Docker repository digest' {
        $result = Publish-PortalPrerequisiteImage -Plan $buildPlan -LoginServer companyregistry.azurecr.io -ImageReference 'deployment-portal:new-release' -Confirm:$false
        $result.Digest | Should -Be $published
        $commands.Count | Should -Be 3
        $commands[0][0] | Should -Be 'build'
        $commands[0] | Should -Contain 'linux/amd64'
        $commands[0][-1] | Should -Be (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)
        $commands[1][0] | Should -Be 'push'
        $commands[2][0] | Should -Be 'image'
    }

    It 'Does not run native commands during WhatIf' {
        (Publish-PortalPrerequisiteImage -Plan $buildPlan -LoginServer companyregistry.azurecr.io -ImageReference 'deployment-portal:new-release' -WhatIf).Status | Should -Be 'Cancelled'
        Should -Invoke az -Times 0 -Exactly
        Should -Invoke docker -Times 0 -Exactly
    }

    It 'Stops at the failed <Stage> before claiming publication' -ForEach @(
        @{ Stage='login'; Expected=0 }, @{ Stage='build'; Expected=1 },
        @{ Stage='push'; Expected=2 }, @{ Stage='image'; Expected=3 }, @{ Stage='digest'; Expected=3 }
    ) {
        $failure=$Stage
        { Publish-PortalPrerequisiteImage -Plan $buildPlan -LoginServer companyregistry.azurecr.io -ImageReference 'deployment-portal:new-release' -Confirm:$false } | Should -Throw
        $commands.Count | Should -Be $Expected
    }

    It 'Passes the pip file only as a BuildKit secret, never as a build argument' {
        $pip = Join-Path $TestDrive 'approved-pip.ini'
        New-Item -Path $pip -ItemType File | Out-Null
        $null = Publish-PortalPrerequisiteImage -Plan $buildPlan -LoginServer companyregistry.azurecr.io -ImageReference 'deployment-portal:new-release' -PipConfigPath $pip -Confirm:$false
        $commands[0] | Should -Contain '--secret'
        $commands[0] | Should -Contain "id=pip_config,src=$pip"
        $commands[0] | Should -Not -Contain '--build-arg'
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

    It 'Updates only the image while preserving host configuration and data paths' {
        $snapshot.Site.properties.state = 'Running'
        $snapshot.Web.etag = 'web-version'
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        $state = @{ stopped=$false; updated=$false; writes=[System.Collections.Generic.List[object]]::new() }
        $original = $snapshot | ConvertTo-Json -Depth 50 -Compress
        Mock Invoke-PortalArm {
            param($Path, $Method, $Body)
            if ($Path -like '*/stop?*') { $state.stopped=$true; return @{} }
            if ($Path -like '*/start?*') { return @{} }
            if ($Method -eq 'PUT') { $state.updated=$true; $state.writes.Add($Body); return @{} }
            if ($Path -like '*/config/web?*') {
                $properties = @{} + $snapshot.Web.properties
                if ($state.updated) { $properties.linuxFxVersion = "DOCKER|$($plan.Image)" }
                return @{ properties=$properties; etag='web-version' }
            }
            if ($Path -like '*/config/authsettingsV2?*') { return $snapshot.Authentication }
            if ($Path -like '*/config/appsettings/list?*') { return $snapshot.Settings }
            $properties = @{} + $snapshot.Site.properties
            if ($state.stopped) { $properties.state='Stopped' }
            return @{ properties=$properties; identity=$snapshot.Site.identity }
        }
        $result = Set-PortalImageUpdate -Plan $plan -Snapshot $snapshot -Subscription $subscription -Confirm:$false
        $result.Status | Should -Be 'ImageUpdated'
        $result.RuntimeVerified | Should -BeFalse
        $state.writes.Count | Should -Be 1
        foreach ($key in $snapshot.Web.properties.Keys) {
            if ($key -ne 'linuxFxVersion') { $state.writes[0].properties[$key] | Should -Be $snapshot.Web.properties[$key] }
        }
        ($snapshot | ConvertTo-Json -Depth 50 -Compress) | Should -Be $original
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -like '*/config/web?*' -and $ETag -eq 'web-version' }
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -notlike '*/config/web?*' }
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Path -like '*/start?*' }
    }

    It 'Does no image update or stop in WhatIf' {
        Mock Invoke-PortalArm { throw 'Unexpected write or read after preview' }
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        (Set-PortalImageUpdate -Plan $plan -Snapshot $snapshot -Subscription $subscription -WhatIf).Status | Should -Be 'PreviewOnly'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
    }

    It 'Refuses a maintenance update on a running app' {
        $snapshot.Site.properties.state = 'Running'
        Mock Invoke-PortalArm { throw 'Unexpected Azure request' }
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        { Set-PortalImageUpdate -Plan $plan -Snapshot $snapshot -Subscription $subscription -MaintenanceReady -Confirm:$false } | Should -Throw '*stopped before the backup*'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
    }

    Context 'Build and deploy existing site updates' {
        BeforeEach {
            $snapshot.Site.properties.state = 'Running'
            $snapshot.Web.properties.linuxFxVersion = "DOCKER|companyregistry.azurecr.io/deployment-portal@$digest"
            $snapshot.Web.properties.acrUseManagedIdentityCreds = $true
            $snapshot.Settings.properties.GALLERY_ADMIN_OIDS = $admin
            $snapshot.Settings.properties.WEBSITES_ENABLE_APP_SERVICE_STORAGE = 'true'
            $snapshot.Settings.properties.WEBSITES_PORT = '8000'
            $updateInputs = @{ SubscriptionId=$subscription; ResourceGroupName='test'; AppName='portal'; RegistryName='companyregistry'; NonInteractive=$true }
            $publishedDigest = 'sha256:' + ('b' * 64)
            Mock Get-Command { return @{ Name=$Name } } -ParameterFilter { $Name -in @('az','docker') }
            Mock Invoke-PortalArm { return $snapshot.Web }
            Mock Get-PortalSnapshot { return $snapshot }
            Mock Invoke-PortalAzJson {
                if ($Arguments[1] -eq 'repository') { return @('original-release') }
                if ($Arguments[1] -eq 'manifest') { return @{ digest=$publishedDigest } }
                throw 'Unexpected CLI operation'
            }
            Mock Publish-PortalPrerequisiteImage { return @{ Status='ImagePublished'; Digest=$publishedDigest } }
            Mock Set-PortalImageUpdate { return @{ Status='ImageUpdated'; Image=$Plan.Image; PreviousRuntime=$Plan.PreviousRuntime } }
            Mock Test-PortalEntraRegistration { throw 'Update must not perform directory setup' }
            Mock Read-Host { throw 'Unexpected prompt' }
            Mock Write-Host {}
            Mock Write-Warning {}
        }

        It 'Previews a new release without Docker, publication, app changes or prompts' {
            Mock Get-Command { throw 'WhatIf must not require Docker' } -ParameterFilter { $Name -eq 'docker' }
            $result = Invoke-PortalImageUpdate @updateInputs -WhatIf
            $result.Status | Should -Be 'PreviewOnly'
            $result.Image | Should -Match '^companyregistry.azurecr.io/deployment-portal:update-\d{8}-\d{6}-[0-9a-f]{8}$'
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Set-PortalImageUpdate -Times 0 -Exactly
            Should -Invoke Read-Host -Times 0 -Exactly
            Should -Invoke Test-PortalEntraRegistration -Times 0 -Exactly
            Should -Invoke Write-Warning -Times 0 -Exactly
            Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Routine image update.*' }
        }

        It 'Reports startup and the selected target before the first remote read' {
            Mock Invoke-PortalArm {
                Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq '[INFO] [1/5] Resolve local source and deployment target.' }
                Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq "[INFO] Target: $target" }
                Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq '[INFO] [2/5] Read current App Service image (Azure authorization + ARM) - starting; waiting for the read-only response...' }
                return $snapshot.Web
            }
            $null = Invoke-PortalImageUpdate @updateInputs -WhatIf
            Should -Invoke Get-PortalSnapshot -Times 1 -Exactly -ParameterFilter { $ReportProgress }
            Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Preview complete in*s. No image was built or pushed; the app was not changed.*' }
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Set-PortalImageUpdate -Times 0 -Exactly
        }

        It 'Pins the actual published digest and preserves live administrator eligibility' {
            $result = Invoke-PortalImageUpdate @updateInputs -Image 'deployment-portal:release-new' -BackupConfirmed -Confirm:$false
            $result.Image | Should -Be "companyregistry.azurecr.io/deployment-portal@$publishedDigest"
            $result.PreviousRuntime | Should -Be "DOCKER|companyregistry.azurecr.io/deployment-portal@$digest"
            Should -Invoke Publish-PortalPrerequisiteImage -Times 1 -Exactly -ParameterFilter { $ImageReference -eq 'deployment-portal:release-new' -and $Plan.Inputs.RegistryName -eq 'companyregistry' }
            Should -Invoke Set-PortalImageUpdate -Times 1 -Exactly -ParameterFilter { $Plan.AdministratorIds -eq $admin -and $Plan.Image.EndsWith($publishedDigest) }
            Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter { $Arguments -contains "deployment-portal@$publishedDigest" }
        }

        It 'Loads only the saved deployment target and rechecks its tenant' {
            $path = Join-Path $TestDrive 'saved.json'
            New-Item -Path $path -ItemType File | Out-Null
            Mock Import-PortalPrepareConfiguration { return @{ TenantId=$tenant; Selections=@{ SubscriptionId=$subscription; ResourceGroupName='test'; AppName='portal'; RegistryName='companyregistry'; AdminObjectIds=@($client) } } }
            (Invoke-PortalImageUpdate -ConfigPath $path -NonInteractive -WhatIf).Status | Should -Be 'PreviewOnly'
            Should -Invoke Get-PortalSnapshot -Times 1 -Exactly -ParameterFilter { $Subscription -eq $subscription -and $AppResourceId -eq $target -and $Registry -eq 'companyregistry' }
            $snapshot.Subscription.tenantId = $client
            { Invoke-PortalImageUpdate -ConfigPath $path -NonInteractive -WhatIf } | Should -Throw '*tenant or selected registry*'
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
        }

        It 'Does not combine a saved configuration with explicit targets' {
            { Invoke-PortalImageUpdate @updateInputs -ConfigPath 'unused.json' -WhatIf } | Should -Throw '*do not mix*'
            Should -Invoke Get-PortalSnapshot -Times 0 -Exactly
        }

        It 'Rejects existing tags, other repositories and digests before building' -ForEach @('deployment-portal:original-release','different:release','deployment-portal@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') {
            { Invoke-PortalImageUpdate @updateInputs -Image $_ -BackupConfirmed -Confirm:$false } | Should -Throw
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Set-PortalImageUpdate -Times 0 -Exactly
        }

        It 'Rejects wrong registry and missing persistence configuration before building' -ForEach @('registry','storage','port','pull') {
            switch ($_) {
                'registry' { $snapshot.Registry.loginServer='anotherregistry.azurecr.io' }
                'storage' { $snapshot.Settings.properties.WEBSITES_ENABLE_APP_SERVICE_STORAGE='false' }
                'port' { $snapshot.Settings.properties.WEBSITES_PORT='8080' }
                'pull' { $snapshot.Web.properties.acrUseManagedIdentityCreds=$false }
            }
            { Invoke-PortalImageUpdate @updateInputs -BackupConfirmed -Confirm:$false } | Should -Throw
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
        }

        It 'Requires explicit unattended confirmation for routine updates' {
            { Invoke-PortalImageUpdate @updateInputs } | Should -Throw '*Confirm*'
            { Invoke-PortalImageUpdate @updateInputs -Confirm } | Should -Throw '*Confirm*'
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Read-Host -Times 0 -Exactly
        }

        It 'Updates routinely without backup acknowledgement in <RunMode> mode' -ForEach @(
            @{ RunMode='interactive'; Unattended=$false }, @{ RunMode='unattended'; Unattended=$true }
        ) {
            $updateInputs.NonInteractive=$Unattended
            (Invoke-PortalImageUpdate @updateInputs -Confirm:$false).Status | Should -Be 'ImageUpdated'
            Should -Invoke Read-Host -Times 0 -Exactly
            Should -Invoke Write-Warning -Times 0 -Exactly
            Should -Invoke Publish-PortalPrerequisiteImage -Times 1 -Exactly
            Should -Invoke Set-PortalImageUpdate -Times 1 -Exactly -ParameterFilter { -not $MaintenanceReady -and $Confirm -eq $false }
        }

        It 'Requires backup acknowledgement for unattended maintenance without prompting' {
            $snapshot.Site.properties.state='Stopped'
            { Invoke-PortalImageUpdate @updateInputs -MaintenanceReady -Confirm:$false } | Should -Throw '*BackupConfirmed*'
            Should -Invoke Read-Host -Times 0 -Exactly
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Set-PortalImageUpdate -Times 0 -Exactly
        }

        It 'Prompts only for explicit maintenance and respects <Answer>' -ForEach @(
            @{ Answer='YES'; Expected='ImageUpdated'; Applied=1 }, @{ Answer='NO'; Expected='Cancelled'; Applied=0 }
        ) {
            $snapshot.Site.properties.state='Stopped'
            $updateInputs.NonInteractive=$false
            Mock Read-Host { return $Answer }
            (Invoke-PortalImageUpdate @updateInputs -MaintenanceReady -Confirm:$false).Status | Should -Be $Expected
            Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $Prompt -like 'Maintenance update:*Type YES to proceed' }
            Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter { $Message -like '*Maintenance mode selected:*' }
            Should -Invoke Publish-PortalPrerequisiteImage -Times $Applied -Exactly
            Should -Invoke Set-PortalImageUpdate -Times $Applied -Exactly
        }

        It 'Previews maintenance without requiring a backup acknowledgement or a stopped app' {
            (Invoke-PortalImageUpdate @updateInputs -MaintenanceReady -WhatIf).Status | Should -Be 'PreviewOnly'
            Should -Invoke Read-Host -Times 0 -Exactly
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Set-PortalImageUpdate -Times 0 -Exactly
            Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter { $Message -like '*Maintenance mode selected:*' }
        }

        It 'Does not deploy after publication failure or declined publication' {
            Mock Publish-PortalPrerequisiteImage { throw 'Build failed' }
            { Invoke-PortalImageUpdate @updateInputs -BackupConfirmed -Confirm:$false } | Should -Throw '*Build failed*'
            Mock Publish-PortalPrerequisiteImage { return @{ Status='Cancelled' } }
            (Invoke-PortalImageUpdate @updateInputs -BackupConfirmed -Confirm:$false).Status | Should -Be 'Cancelled'
            Should -Invoke Set-PortalImageUpdate -Times 0 -Exactly
        }

        It 'Does not deploy a missing or mismatched published digest' -ForEach @('missing','mismatch') {
            if ($_ -eq 'missing') { Mock Publish-PortalPrerequisiteImage { return @{ Status='ImagePublished' } } }
            else { Mock Invoke-PortalAzJson { if ($Arguments[1] -eq 'repository') { return @() }; return @{ digest=$digest } } }
            { Invoke-PortalImageUpdate @updateInputs -BackupConfirmed -Confirm:$false } | Should -Throw '*digest*'
            Should -Invoke Set-PortalImageUpdate -Times 0 -Exactly
        }

        It 'Requires a stopped app for acknowledged maintenance and forwards that mode' {
            { Invoke-PortalImageUpdate @updateInputs -MaintenanceReady -BackupConfirmed -Confirm:$false } | Should -Throw '*already stopped*'
            $snapshot.Site.properties.state='Stopped'
            (Invoke-PortalImageUpdate @updateInputs -MaintenanceReady -BackupConfirmed -Confirm:$false).Status | Should -Be 'ImageUpdated'
            Should -Invoke Set-PortalImageUpdate -Times 1 -Exactly -ParameterFilter { $MaintenanceReady }
        }
    }

    It 'Refuses image updates when configuration drifts before stop' {
        $snapshot.Site.properties.state = 'Running'
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        Mock Invoke-PortalArm { return @{ properties=@{ changed=$true } } }
        { Set-PortalImageUpdate -Plan $plan -Snapshot $snapshot -Subscription $subscription -Confirm:$false } | Should -Throw '*changed during the build*'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' -or $Path -like '*/stop?*' -or $Path -like '*/start?*' }
    }

    It 'Leaves the app for inspection if stop or readback cannot be verified' -ForEach @('stop','readback') {
        $failurePoint = $_
        $snapshot.Site.properties.state = 'Running'
        $plan = Get-PortalDeploymentPlan $snapshot $target 'deployment-portal:release-1' @($admin)
        $operationState = @{ stopped=$false }
        Mock Invoke-PortalArm {
            param($Path, $Method)
            if ($Path -like '*/stop?*') { $operationState.stopped=$true; return @{} }
            if ($Method -eq 'PUT') { return @{} }
            if ($Path -like '*/config/web?*') { return $snapshot.Web }
            if ($Path -like '*/config/appsettings/list?*') { return $snapshot.Settings }
            if ($Path -like '*/config/authsettingsV2?*') { return $snapshot.Authentication }
            $properties = @{} + $snapshot.Site.properties
            if ($operationState.stopped -and $failurePoint -ne 'stop') { $properties.state='Stopped' }
            return @{ properties=$properties; identity=$snapshot.Site.identity }
        }
        { Set-PortalImageUpdate -Plan $plan -Snapshot $snapshot -Subscription $subscription -Confirm:$false } | Should -Throw '*may be stopped or the image may already be changed*'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Path -like '*/start?*' }
        if ($failurePoint -eq 'stop') { Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' } }
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