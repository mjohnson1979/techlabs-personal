BeforeAll {
    . "$PSScriptRoot/../Reset-Portal.ps1"
    $subscription = '11111111-1111-4111-8111-111111111111'
    $tenant = '22222222-2222-4222-8222-222222222222'
    $client = '33333333-3333-4333-8333-333333333333'
    $admin = '44444444-4444-4444-8444-444444444444'
    $target = "/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Web/sites/portal"
}

Describe 'Reversible portal reset' {
    BeforeEach {
        $snapshot = @{
            Subscription = @{id=$subscription;tenantId=$tenant;environmentName='AzureCloud';state='Enabled'}
            Site = @{id=$target;kind='app,linux';properties=@{state='Running';httpsOnly=$true};identity=@{type='SystemAssigned';principalId=$admin}}
            Web = @{properties=@{linuxFxVersion=('DOCKER|company.azurecr.io/deployment-portal@sha256:' + ('a' * 64));alwaysOn=$false}}
            Plan = @{properties=@{numberOfWorkers=1;elasticScaleEnabled=$false}}
            Authentication = @{properties=@{
                platform=@{enabled=$true};httpSettings=@{requireHttps=$true}
                globalValidation=@{requireAuthentication=$true;excludedPaths=@()}
                identityProviders=@{azureActiveDirectory=@{enabled=$true;registration=@{clientId=$client;openIdIssuer="https://login.microsoftonline.com/$tenant/v2.0";clientSecretSettingName='ENTRA_SECRET'};validation=@{allowedAudiences=@($client)}}}
            }}
            Settings = @{properties=@{WEBSITES_ENABLE_APP_SERVICE_STORAGE='true';GALLERY_EASY_AUTH='true';GALLERY_ADMIN_OIDS=$admin;ENTRA_SECRET='synthetic-private';GALLERY_GITHUB_TOKEN='synthetic-github';UNRELATED='keep'}}
        }
        Mock Write-Host {}
        Mock Write-Warning {}
        Mock Read-Host { 'portal' }
        Mock Get-PortalResetSnapshot { $snapshot }
        Mock Invoke-PortalAzJson {
            if ($Arguments[1] -eq 'stop') { $snapshot.Site.properties.state='Stopped' }
            if ($Arguments[1] -eq 'start') { $snapshot.Site.properties.state='Running' }
            return @{}
        }
        Mock Invoke-PortalArm {
            $snapshot.Settings = @{properties=$Body.properties}
            return @{}
        }
    }

    It 'Produces a non-secret plan retaining previous paths without mutating settings' {
        $before = $snapshot | ConvertTo-Json -Depth 50
        $plan = Get-PortalResetPlan $snapshot $target
        $plan.PreviousDatabasePath | Should -Be '/home/data/deployment-portal.db'
        $plan.NewDatabasePath | Should -Match '^/home/data/portal-reset/[a-f0-9]{32}/deployment-portal.db$'
        ($plan | ConvertTo-Json) | Should -Not -Match 'synthetic-private|synthetic-github'
        ($snapshot | ConvertTo-Json -Depth 50) | Should -Be $before
    }

    It 'Previews without stopping, changing settings or requiring acknowledgement' {
        $result = Invoke-PortalReset $subscription test portal -NonInteractive -WhatIf
        $result.Status | Should -Be 'PreviewOnly'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
        Should -Invoke Read-Host -Times 0 -Exactly
    }

    It 'Refuses apply without workload acknowledgement' {
        { Invoke-PortalReset $subscription test portal -NonInteractive -Confirm:$false } | Should -Throw '*AcknowledgeUnmanagedDeployments*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Resets paths and connections while preserving Entra, image and all unrelated values' {
        $result = Invoke-PortalReset $subscription test portal -NonInteractive -AcknowledgeUnmanagedDeployments -Confirm:$false
        $result.Status | Should -Be 'ResetConfigured'
        $snapshot.Settings.properties.GALLERY_DB_PATH | Should -Be $result.Plan.NewDatabasePath
        $snapshot.Settings.properties.GALLERY_TF_WORK | Should -Be $result.Plan.NewTerraformPath
        $snapshot.Settings.properties.GALLERY_GITHUB_TOKEN | Should -Be ''
        $snapshot.Settings.properties.GALLERY_SUBSCRIPTION_ID | Should -Be ''
        $snapshot.Settings.properties.ENTRA_SECRET | Should -Be 'synthetic-private'
        $snapshot.Settings.properties.UNRELATED | Should -Be 'keep'
        $snapshot.Settings.properties.GALLERY_ADMIN_OIDS | Should -Be $admin
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -like '*/config/appsettings?api-version=2024-11-01' }
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter { $Arguments[1] -eq 'stop' }
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter { $Arguments[1] -eq 'start' }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'delete' }
    }

    It 'Rejects unsafe prerequisites before any operation' -ForEach @('storage','auth','admins','path','provider-binding') {
        switch ($_) {
            storage { $snapshot.Settings.properties.WEBSITES_ENABLE_APP_SERVICE_STORAGE='false' }
            auth { $snapshot.Authentication.properties.platform.enabled=$false }
            admins { $snapshot.Settings.properties.GALLERY_ADMIN_OIDS='' }
            path { $snapshot.Settings.properties.GALLERY_DB_PATH='/tmp/custom.db' }
            provider-binding { $snapshot.Authentication.properties.identityProviders.azureActiveDirectory.registration.clientSecretSettingName='GALLERY_GITHUB_TOKEN' }
        }
        { Get-PortalResetPlan $snapshot $target } | Should -Throw
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Does not start the app after a failed settings write' {
        Mock Invoke-PortalArm { throw 'write failed' }
        { Invoke-PortalReset $subscription test portal -NonInteractive -AcknowledgeUnmanagedDeployments -Confirm:$false } | Should -Throw '*settings may have changed*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments[1] -eq 'start' }
    }

    It 'Rejects unattended apply without explicit Confirm false' {
        { Invoke-PortalReset $subscription test portal -NonInteractive -AcknowledgeUnmanagedDeployments } | Should -Throw '*Confirm*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Rejects a mismatched typed app name without stopping' {
        Mock Read-Host { 'wrong-app' }
        { Invoke-PortalReset $subscription test portal -AcknowledgeUnmanagedDeployments -Confirm:$false } | Should -Throw '*did not match*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Compares settings independent of property order and catches removed keys' {
        $actual = $snapshot | ConvertTo-Json -Depth 50 | ConvertFrom-Json -AsHashtable
        $reordered = [ordered]@{}
        foreach ($key in @($snapshot.Settings.properties.Keys | Sort-Object)) { $reordered[$key] = $snapshot.Settings.properties[$key] }
        $actual.Settings.properties = $reordered
        { Assert-PortalResetSnapshot $snapshot $actual } | Should -Not -Throw
        $actual.Settings.properties.Remove('UNRELATED')
        { Assert-PortalResetSnapshot $snapshot $actual } | Should -Throw '*App settings changed*'
    }

    It 'Rejects changed identity or networking' -ForEach @('identity','network') {
        $actual = $snapshot | ConvertTo-Json -Depth 50 | ConvertFrom-Json -AsHashtable
        if ($_ -eq 'identity') { $actual.Site.identity.principalId='changed' }
        else { $actual.Site.properties.publicNetworkAccess='Disabled' }
        { Assert-PortalResetSnapshot $snapshot $actual } | Should -Throw
    }

    It 'Does not write settings when stop is not confirmed by readback' {
        Mock Invoke-PortalAzJson { return @{} }
        { Invoke-PortalReset $subscription test portal -NonInteractive -AcknowledgeUnmanagedDeployments -Confirm:$false } | Should -Throw '*settings may have changed*'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
    }

    It 'Does not restart after failed settings readback' {
        Mock Invoke-PortalArm { return @{} }
        { Invoke-PortalReset $subscription test portal -NonInteractive -AcknowledgeUnmanagedDeployments -Confirm:$false } | Should -Throw '*settings may have changed*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments[1] -eq 'start' }
    }

    It 'Rejects multi-instance hosting' {
        $snapshot.Plan.properties.numberOfWorkers=2
        { Get-PortalResetPlan $snapshot $target } | Should -Throw '*single-instance*'
    }

    It 'Preserves prior reset paths for another explicit reset' {
        $snapshot.Settings.properties.GALLERY_DB_PATH='/home/data/portal-reset/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/deployment-portal.db'
        $snapshot.Settings.properties.GALLERY_TF_WORK='/home/data/portal-reset/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/tf-runs'
        $plan = Get-PortalResetPlan $snapshot $target
        $plan.PreviousDatabasePath | Should -Be $snapshot.Settings.properties.GALLERY_DB_PATH
        $plan.NewDatabasePath | Should -Not -Be $plan.PreviousDatabasePath
    }
}

Describe 'Reset command entry point' {
    BeforeAll {
        function az { param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments) throw 'Unexpected unmocked Azure CLI operation' }
    }

    It 'Forwards explicit parameters and WhatIf without any remote write' {
        Mock Read-Host { throw 'Unexpected prompt' }
        Mock Write-Host {}
        Mock Write-Warning {}
        Mock az {
            $global:LASTEXITCODE=0
            $data = switch ($Arguments[0] + ' ' + $Arguments[1]) {
                'account show' { @{id='11111111-1111-4111-8111-111111111111';tenantId='22222222-2222-4222-8222-222222222222';environmentName='AzureCloud';state='Enabled'} }
                'account get-access-token' { @{accessToken='synthetic-token'} }
                default { throw 'Unexpected CLI write' }
            }
            $data | ConvertTo-Json -Depth 20 -Compress
        }
        Mock Invoke-RestMethod {
            $group='/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/test'
            if ($Uri -like '*/config/appsettings/list?*' -and $Method -eq 'POST') {
                return @{properties=@{WEBSITES_ENABLE_APP_SERVICE_STORAGE='true';GALLERY_EASY_AUTH='true';GALLERY_ADMIN_OIDS='44444444-4444-4444-8444-444444444444';ENTRA_SECRET='synthetic-private'}}
            }
            if ($Method -ne 'GET') { throw 'Unexpected ARM write' }
            if ($Uri -like '*/config/authsettingsV2?*') {
                return @{properties=@{platform=@{enabled=$true};httpSettings=@{requireHttps=$true};globalValidation=@{requireAuthentication=$true};identityProviders=@{azureActiveDirectory=@{enabled=$true;registration=@{clientId='33333333-3333-4333-8333-333333333333';openIdIssuer='https://login.microsoftonline.com/22222222-2222-4222-8222-222222222222/v2.0'}}}}}
            }
            if ($Uri -like '*/config/web?*') { return @{properties=@{linuxFxVersion='DOCKER|company.azurecr.io/deployment-portal:release'}} }
            if ($Uri -like '*/serverfarms/plan?*') { return @{properties=@{numberOfWorkers=1;elasticScaleEnabled=$false}} }
            return @{id="$group/providers/Microsoft.Web/sites/portal";kind='app,linux';properties=@{state='Running';httpsOnly=$true;serverFarmId="$group/providers/Microsoft.Web/serverfarms/plan"}}
        }
        $previousExitCode=$global:LASTEXITCODE
        try {
            $result = & "$PSScriptRoot/../Reset-Portal.ps1" -SubscriptionId '11111111-1111-4111-8111-111111111111' -ResourceGroupName test -AppName portal -NonInteractive -WhatIf
            $result.Status | Should -Be 'PreviewOnly'
            $result.Plan.AppResourceId | Should -Be '/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/test/providers/Microsoft.Web/sites/portal'
            ($result | ConvertTo-Json -Depth 20) | Should -Not -Match 'synthetic-private|synthetic-token'
            Should -Invoke Read-Host -Times 0 -Exactly
            Should -Invoke az -Times 0 -Exactly -ParameterFilter { $Arguments[0] -eq 'webapp' }
            Should -Invoke Invoke-RestMethod -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH','DELETE') }
        } finally { $global:LASTEXITCODE=$previousExitCode }
    }
}