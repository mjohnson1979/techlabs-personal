BeforeAll {
    . "$PSScriptRoot/../Initialize-Portal.ps1"
    . "$PSScriptRoot/../Portal-Foundation.ps1"
    $subscription = '11111111-1111-4111-8111-111111111111'
    $tenant = '22222222-2222-4222-8222-222222222222'
    $admin = '33333333-3333-4333-8333-333333333333'
}

Describe 'Prepare configuration files' {
    BeforeEach {
        $configPath = Join-Path $TestDrive 'prepare.json'
        $selections = @{
            SubscriptionId=$subscription; ResourceGroupName='rg-portal'; Location='centralus'
            AppName='app-portal'; PlanName='asp-portal'; RegistryName='crportal'; PlanSku='F1'
            AdminObjectIds=@($admin); EntraMode='Create'; EntraDisplayName='Organization Portal'; CredentialDays=90
        }
    }

    It 'Round trips only non-secret selections even during WhatIf' {
        $selections.ClientSecret='must-not-be-saved'
        $selections.Confirm=$false
        $selections.RepairEntra=$true
        $selections.NonInteractive=$true
        $WhatIfPreference=$true
        Export-PortalPrepareConfiguration $configPath $tenant $selections
        $saved = Import-PortalPrepareConfiguration $configPath
        $saved.TenantId | Should -Be $tenant
        $saved.Selections.PlanSku | Should -Be 'F1'
        $saved.Selections.AdminObjectIds | Should -HaveCount 1
        $saved.Selections.AdminObjectIds[0] | Should -Be $admin
        $saved.Selections.CredentialDays | Should -Be 90
        (Get-Content -LiteralPath $configPath -Raw) | Should -Not -Match 'must-not-be-saved|Confirm|RepairEntra|NonInteractive'
    }

    It 'Rejects malformed, incomplete or approval-bearing configuration: <_>' -ForEach @('malformed','missing','approval','version','lifetime','type') {
        $case = $_
        Export-PortalPrepareConfiguration $configPath $tenant $selections
        $saved = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json -AsHashtable
        switch ($case) {
            'missing' { $saved.Selections.Remove('AppName') }
            'approval' { $saved.Selections.Confirm=$false }
            'version' { $saved.SchemaVersion=2 }
            'lifetime' { $saved.Selections.CredentialDays=0 }
            'type' { $saved.Selections.PlanSku=@{ Value='F1' } }
        }
        $content = if ($case -eq 'malformed') { '{broken' } else { $saved | ConvertTo-Json -Depth 6 }
        [IO.File]::WriteAllText($configPath, $content)
        { Import-PortalPrepareConfiguration $configPath } | Should -Throw '*invalid or unreadable*'
    }

    It 'Preserves the previous configuration if new selections are invalid' {
        Export-PortalPrepareConfiguration $configPath $tenant $selections
        $before = Get-Content -LiteralPath $configPath -Raw
        $selections.PlanSku='unsupported'
        { Export-PortalPrepareConfiguration $configPath $tenant $selections } | Should -Throw '*invalid or unreadable*'
        (Get-Content -LiteralPath $configPath -Raw) | Should -BeExactly $before
        @(Get-ChildItem -LiteralPath $TestDrive -Filter '*.tmp') | Should -HaveCount 0
    }
}

Describe 'Prepare saved selection workflow' {
    BeforeEach {
        $configPath = Join-Path $TestDrive 'workflow-prepare.json'
        if (Test-Path -LiteralPath $configPath) { Remove-Item -LiteralPath $configPath }
        $selectionArgs = @{
            SubscriptionId=$subscription; ResourceGroupName='rg-portal'; Location='centralus'
            AppName='app-portal'; PlanName='asp-portal'; RegistryName='crportal'; PlanSku='F1'
            AdminObjectIds=@($admin); EntraMode='Existing'; EntraClientId=$tenant; CredentialDays=45
        }
        $snapshot = @{ Account=@{ id=$subscription; tenantId=$tenant; state='Enabled'; environmentName='AzureCloud' } }
        $readiness = @{ Status='Ready'; Checks=@(@{ Check='Entra callback'; Status='Ready'; Detail='Configured' }) }
        Mock Get-Command { @{ Name='az' } } -ParameterFilter { $Name -eq 'az' }
        Mock Write-Host {}
        Mock Read-Host { throw 'Unexpected selection or secret prompt' }
        Mock Get-PortalDiscoveredInputs { throw 'Unexpected discovery' }
        Mock Get-PortalPrerequisiteSnapshot { $snapshot }
        Mock Get-PortalEntraReadiness { $readiness }
        Mock Set-PortalPrerequisites { @{ Status='Cancelled' } }
        Mock Repair-PortalEntraReadiness { throw 'Unexpected repair' }
        Mock Invoke-PortalAzJson { throw 'Unexpected Azure call' }
        Mock Invoke-PortalArm { throw 'Unexpected Azure call' }
    }

    It 'Saves a successful preview then reuses selections for interactive apply without discovery' {
        $preview = Initialize-PortalHosting @selectionArgs -ConfigPath $configPath -NonInteractive -WhatIf
        $preview.ConfigurationSaved | Should -BeTrue
        $preview.ConfigurationPath | Should -Be $configPath
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
        (Initialize-PortalHosting -ConfigPath $configPath).Status | Should -Be 'Cancelled'
        Should -Invoke Get-PortalPrerequisiteSnapshot -Times 2 -Exactly -ParameterFilter {
            $Inputs.AppName -eq 'app-portal' -and $Inputs.SubscriptionId -eq $subscription -and $Inputs.PlanSku -eq 'F1'
        }
        Should -Invoke Get-PortalEntraReadiness -Times 2 -Exactly -ParameterFilter { $ClientId -eq $tenant -and $Mode -eq 'Existing' }
        Should -Invoke Get-PortalDiscoveredInputs -Times 0 -Exactly
        Should -Invoke Read-Host -Times 0 -Exactly
        Should -Invoke Set-PortalPrerequisites -Times 1 -Exactly
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
    }

    It 'Reuses Create mode and credential lifetime without asking the options again' {
        $selectionArgs.EntraMode='Create'
        $selectionArgs.EntraDisplayName='Organization Portal'
        Initialize-PortalHosting @selectionArgs -ConfigPath $configPath -NonInteractive -WhatIf | Out-Null
        $preview = Initialize-PortalHosting -ConfigPath $configPath -WhatIf
        $preview.ConfigurationSaved | Should -BeTrue
        (Import-PortalPrepareConfiguration $configPath).Selections.CredentialDays | Should -Be 45
        Should -Invoke Read-Host -Times 0 -Exactly
        Should -Invoke Get-PortalDiscoveredInputs -Times 0 -Exactly
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
    }

    It 'Does not save a blocked preview or deferred Entra: <_>' -ForEach @('Blocked','RequiresRepair','Later') {
        $case = $_
        if ($case -eq 'Later') { $selectionArgs.EntraMode='Later'; $readiness.Status='Deferred' }
        else { $readiness.Status=$case }
        $preview = Initialize-PortalHosting @selectionArgs -ConfigPath $configPath -NonInteractive -WhatIf
        $preview.ConfigurationSaved | Should -BeFalse
        $preview.ConfigurationPath | Should -BeNullOrEmpty
        $configPath | Should -Not -Exist
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
    }

    It 'Rechecks readiness and stops apply if Azure is now blocked' {
        Initialize-PortalHosting @selectionArgs -ConfigPath $configPath -NonInteractive -WhatIf | Out-Null
        $before = Get-Content -LiteralPath $configPath -Raw
        $readiness.Status='Blocked'
        $readiness.Checks=@(@{ Check='Directory access'; Status='Blocked'; Detail='Access denied' })
        { Initialize-PortalHosting -ConfigPath $configPath } | Should -Throw '*Preparation blocked*'
        $preview = Initialize-PortalHosting -ConfigPath $configPath -WhatIf
        $preview.ConfigurationSaved | Should -BeFalse
        (Get-Content -LiteralPath $configPath -Raw) | Should -BeExactly $before
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
    }

    It 'Stops before any writes if the subscription tenant changes' {
        Initialize-PortalHosting @selectionArgs -ConfigPath $configPath -NonInteractive -WhatIf | Out-Null
        $snapshot.Account.tenantId=$admin
        { Initialize-PortalHosting -ConfigPath $configPath } | Should -Throw '*saved Prepare tenant*'
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
    }

    It 'Does not restore repair planning or waive unattended confirmation' {
        $readiness.Status='RequiresRepair'
        $readiness.Checks=@(@{ Check='Entra callback'; Status='Repairable'; Detail='Add callback' })
        Initialize-PortalHosting @selectionArgs -ConfigPath $configPath -RepairEntra -NonInteractive -WhatIf | Out-Null
        $preview = Initialize-PortalHosting -ConfigPath $configPath -NonInteractive -WhatIf
        $preview.EntraRepairPlanned | Should -BeFalse
        $preview.ReadyForApply | Should -BeFalse
        { Initialize-PortalHosting -ConfigPath $configPath -NonInteractive } | Should -Throw '*explicit -Confirm*'
        Should -Invoke Repair-PortalEntraReadiness -Times 0 -Exactly
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
    }

    It 'Fresh bypasses an invalid saved file and starts discovery' {
        [IO.File]::WriteAllText($configPath, '{broken')
        { Initialize-PortalHosting -ConfigPath $configPath -Fresh -WhatIf } | Should -Throw '*Unexpected discovery*'
        Should -Invoke Get-PortalDiscoveredInputs -Times 1 -Exactly
        Should -Invoke Get-PortalPrerequisiteSnapshot -Times 0 -Exactly
    }

    It 'Does not combine explicit targets with saved selections' {
        Initialize-PortalHosting @selectionArgs -ConfigPath $configPath -NonInteractive -WhatIf | Out-Null
        { Initialize-PortalHosting -ConfigPath $configPath -AppName 'other-app' -WhatIf } | Should -Throw '*Unexpected discovery*'
        Should -Invoke Get-PortalDiscoveredInputs -Times 1 -Exactly -ParameterFilter { $Values.AppName -eq 'other-app' -and -not $Values.SubscriptionId }
    }

    It 'Fails closed on a missing or invalid explicitly selected configuration: <_>' -ForEach @('missing','invalid') {
        if ($_ -eq 'invalid') { [IO.File]::WriteAllText($configPath, '{broken') }
        { Initialize-PortalHosting -ConfigPath $configPath } | Should -Throw '*configuration*'
        Should -Invoke Get-PortalDiscoveredInputs -Times 0 -Exactly
        Should -Invoke Get-PortalPrerequisiteSnapshot -Times 0 -Exactly
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
    }
}

Describe 'Prerequisite image references' {
    It 'Accepts tagged image <_> for build and deployment' -ForEach @('deployment-portal:latest','deployment-portal:release-1','team/portal:Release_1') {
        Test-PortalPrerequisiteImageReference $_ -ForBuild | Should -BeTrue
        Test-PortalPrerequisiteImageReference $_ | Should -BeTrue
    }

    It 'Accepts immutable digests only for deployment' {
        $reference = 'deployment-portal@sha256:' + ('a' * 64)
        Test-PortalPrerequisiteImageReference $reference | Should -BeTrue
        Test-PortalPrerequisiteImageReference $reference -ForBuild | Should -BeFalse
    }

    It 'Rejects malformed or tag-only input <_>' -ForEach @('latest','','--help','portal','portal:','portal//app:tag','../portal:tag','portal/:tag','portal..app:tag','PORTAL:tag','portal;command:tag','https://registry.azurecr.io/portal:tag','portal@sha256:bad',"portal:tag`n") {
        Test-PortalPrerequisiteImageReference $_ -ForBuild | Should -BeFalse
        Test-PortalPrerequisiteImageReference $_ | Should -BeFalse
    }

    It 'Rejects tag-only publication before invoking external tools' {
        Mock Get-Command { throw 'Unexpected Docker check' }
        { Publish-PortalPrerequisiteImage -Plan @{} -LoginServer 'registry.azurecr.io' -ImageReference latest -Confirm:$false } | Should -Throw '*deployment-portal:latest*'
        Should -Invoke Get-Command -Times 0 -Exactly
    }
}

Describe 'Scoped host role lookup' {
    BeforeEach {
        $roleId = "/subscriptions/$subscription/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7"
        $scope = "/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Web/sites/portal"
        $existingAssignments = @()
        Mock Invoke-PortalAzJson {
            if ($Operation -eq 'role definition lookup') { return @{ id=$roleId } }
            if ($Operation -eq 'existing role assignments') {
                if ($Arguments -contains '--all') { throw 'group or scope are not required when --all is used' }
                return $existingAssignments
            }
            if ($Operation -eq 'scoped host role assignment') { return @{} }
            throw 'Unexpected CLI operation'
        }
    }

    It 'Reuses a matching <_> assignment without issuing a grant' -ForEach @('direct','inherited') {
        $assignmentScope = if ($_ -eq 'direct') { $scope } else { "/subscriptions/$subscription" }
        $existingAssignments = @(@{ principalId=$admin; roleDefinitionId=$roleId; scope=$assignmentScope })
        Grant-PortalHostRole $subscription $admin $scope 'Reader'
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter {
            $Operation -eq 'existing role assignments' -and
            ($Arguments -join ' ') -eq "role assignment list --subscription $subscription --scope $scope --include-inherited --assignee-object-id $admin --fill-principal-name false --fill-role-definition-name false"
        }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'create' }
    }

    It 'Grants only the requested role at the requested scope when no matching assignment exists' {
        $existingAssignments = @(@{ principalId=$tenant; roleDefinitionId=$roleId }, @{ principalId=$admin; roleDefinitionId='other-role' })
        Grant-PortalHostRole $subscription $admin $scope 'Reader'
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter {
            ($Arguments -join ' ') -eq "role assignment create --subscription $subscription --assignee-object-id $admin --assignee-principal-type ServicePrincipal --scope $scope --role Reader"
        }
    }

    It 'Does not attempt a grant when assignment reads fail' {
        Mock Invoke-PortalAzJson { throw 'Read denied' } -ParameterFilter { $Operation -eq 'existing role assignments' }
        { Grant-PortalHostRole $subscription $admin $scope 'Reader' } | Should -Throw '*Read denied*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'create' }
    }
}

Describe 'Interactive prerequisite planning' {
    BeforeEach {
        $inputs = @{
            ConfigPath = (Join-Path $TestDrive 'prepare.json')
            SubscriptionId = $subscription; ResourceGroupName = 'rg-portal-test-centralus-001'
            Location = 'centralus'; AppName = 'app-portal-test-centralus-001'; PlanName = 'asp-portal-test-centralus-001'
            RegistryName = 'crportaltestcentralus001'; PlanSku = 'B1'; AdminObjectIds = @($admin)
        }
        $snapshot = @{
            Account = @{ id = $subscription; tenantId = $tenant; state = 'Enabled'; environmentName = 'AzureCloud' }
        }
        Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Later' } }
    }

    It 'Plans missing resources without assuming a company VPN exists' {
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        $plan.TenantId | Should -Be $tenant
        $plan.Steps | Should -HaveCount 6
        $plan.Steps[2].Action | Should -Match 'Basic'
        $plan.Inputs.ContainsKey('IntegrationSubnetId') | Should -BeFalse
    }

    It 'Allows a new F1 plan with a nonblocking Always On warning' {
        $inputs.PlanSku = 'F1'
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        $plan.HostingReadiness.Status | Should -Be 'Warning'
        $plan.HostingReadiness.TargetSku | Should -Be 'F1'
        $plan.HostingReadiness.UpgradeRequired | Should -BeFalse
        $plan.Steps[1].Action | Should -Be 'Create single-instance Linux F1 plan'
    }



    It 'Rejects invalid administrator input' {
        $inputs.AdminObjectIds = @('user@example.com')
        { Get-PortalPrerequisitePlan $inputs $snapshot } | Should -Throw '*GUID*'
    }

    It 'Includes missing provider registration in the reviewed plan' {
        $snapshot.Providers = @(@{ namespace = 'Microsoft.ContainerRegistry'; registrationState = 'NotRegistered' })
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        $plan.Steps | Should -HaveCount 7
        $plan.Steps[0].Resource | Should -Be 'Microsoft.ContainerRegistry'
    }

    It 'Has no Azure mutations in prerequisite WhatIf' {
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        Mock Invoke-PortalPrerequisiteResource { throw 'Unexpected cloud mutation' }
        Mock Invoke-PortalArm { throw 'Unexpected cloud mutation' }
        Mock Invoke-PortalAzJson { throw 'Unexpected cloud mutation' }
        (Set-PortalPrerequisites -Plan $plan -WhatIf).Status | Should -Be 'PreviewOnly'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Does not overwrite or wait for an existing completed resource during create-or-reuse' {
        Mock Invoke-PortalPrerequisiteResource { return @{ id = 'existing'; properties=@{ provisioningState='Succeeded' } } }
        Mock Invoke-PortalAzJson { throw 'Unexpected provisioning wait' }
        (New-PortalPrerequisiteResource $subscription '/existing' '2024-11-01' @{}).id | Should -Be 'existing'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Waits for a newly created resource without resubmitting its creation' {
        $script:provisioningFinished = $false
        Mock Invoke-PortalPrerequisiteResource {
            if ($Method -eq 'PUT') { return @{ properties = @{ provisioningState = 'Creating' } } }
            if ($script:provisioningFinished) { return @{ properties = @{ provisioningState = 'Succeeded'; loginServer='registry.azurecr.io' } } }
            return $null
        }
        Mock Invoke-PortalAzJson { $script:provisioningFinished = $true }
        $result = New-PortalPrerequisiteResource $subscription '/existing' '2023-07-01' @{}
        $result.properties.provisioningState | Should -Be 'Succeeded'
        $result.properties.loginServer | Should -Be 'registry.azurecr.io'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $CreateOnly }
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter {
            ($Arguments -join ' ') -eq "resource wait --subscription $subscription --ids /existing --api-version 2023-07-01 --created --interval 10 --timeout 600"
        }
    }

    It 'Waits for an existing resource in <_> state without recreating it' -ForEach @('Accepted','Creating','Updating','Running') {
        $initialState = $_
        $script:provisioningFinished = $false
        Mock Invoke-PortalPrerequisiteResource { return @{ properties=@{ provisioningState=$(if ($script:provisioningFinished) { 'Succeeded' } else { $initialState }) } } }
        Mock Invoke-PortalAzJson { $script:provisioningFinished = $true }
        (New-PortalPrerequisiteResource $subscription '/existing' '2023-07-01' @{} -RequireExisting).properties.provisioningState | Should -Be 'Succeeded'
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH') }
    }

    It 'Rejects existing resources in terminal state <_> without writes or retries' -ForEach @('Failed','Canceled','Deleting') {
        $terminalState = $_
        Mock Invoke-PortalPrerequisiteResource { return @{ properties=@{ provisioningState=$terminalState } } }
        Mock Invoke-PortalAzJson { throw 'Unexpected wait' }
        { New-PortalPrerequisiteResource $subscription '/existing' '2023-07-01' @{} } | Should -Throw "*Azure reports '$terminalState'*"
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH') }
    }

    It 'Stops safely when the bounded provisioning wait fails' {
        Mock Invoke-PortalPrerequisiteResource { return @{ properties=@{ provisioningState='Creating' } } }
        Mock Invoke-PortalAzJson { throw 'synthetic-sensitive-error' }
        { New-PortalPrerequisiteResource $subscription '/existing' '2023-07-01' @{} } | Should -Throw '*wait timed out or failed*no resource was recreated or deleted*'
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { $Method -notin @('PUT','PATCH') }
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH') }
    }

    It 'Requires Succeeded on readback even when the CLI wait returns successfully' -ForEach @('Creating','Failed','') {
        $finalState = $_
        $script:provisioningFinished = $false
        Mock Invoke-PortalPrerequisiteResource { return @{ properties=@{ provisioningState=$(if ($script:provisioningFinished) { $finalState } else { 'Creating' }) } } }
        Mock Invoke-PortalAzJson { $script:provisioningFinished = $true }
        { New-PortalPrerequisiteResource $subscription '/existing' '2023-07-01' @{} } | Should -Throw '*completion could not be verified*'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH') }
    }

    It 'Rejects broad host role grants' {
        Mock Invoke-PortalAzJson { throw 'Unexpected Azure request' }
        { Grant-PortalHostRole $subscription $admin "/subscriptions/$subscription" 'Owner' } | Should -Throw '*Unapproved*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }



    It 'Does not prompt for a secret or write Entra settings in WhatIf' {
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        Mock Test-PortalEntraRegistration {}
        Mock Invoke-PortalArm { return @{ properties = @{ identityProviders = @{} } } }
        Mock Read-Host { throw 'Unexpected secret prompt' }
        Connect-PortalPrerequisiteEntra $plan 'https://app.example.com' $admin -WhatIf
        Should -Invoke Read-Host -Times 0 -Exactly
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' }
    }

    It 'Requires an existing secret store for unattended Entra configuration' {
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        Mock Test-PortalEntraRegistration {}
        Mock Invoke-PortalArm { return @{ properties = @{ identityProviders = @{} } } }
        { Connect-PortalPrerequisiteEntra $plan 'https://app.example.com' $admin -NonInteractive -Confirm:$false } | Should -Throw '*Preconfigure*'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' }
    }

    It 'Explains the secret Value and storage before masked entry without exposing it' {
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        $messages = [Collections.Generic.List[string]]::new()
        Mock Test-PortalEntraRegistration {}
        Mock Invoke-PortalArm { return @{ properties = @{ identityProviders = @{} } } }
        Mock Write-Host {}
        Mock Write-PortalStatus { $messages.Add("$Status $Message") }
        Mock Export-PortalPrepareConfiguration { throw 'Unexpected local configuration write' }
        Mock Read-Host {
            $guidance = $messages -join "`n"
            $guidance | Should -Match 'application password.*not your personal account password'
            $guidance | Should -Match ([regex]::Escape("Application (client) ID: $admin in tenant $tenant"))
            $guidance | Should -Match 'Certificates & secrets > Client secrets.*Value field, not the Secret ID'
            $guidance | Should -Match 'only when it is created.*authorized administrator can add a new client secret.*Keep existing secrets'
            $guidance | Should -Match 'never in chat.*masks the characters'
            $guidance | Should -Match 'protected App Service setting PORTAL_ENTRA_CLIENT_SECRET.*does not print.*local files.*saved Prepare configuration'
            $guidance | Should -Match 'Ctrl\+C.*Hosting already prepared remains'
            $secure = [Security.SecureString]::new()
            foreach ($character in 'synthetic-secret-for-prompt-test'.ToCharArray()) { $secure.AppendChar($character) }
            return $secure
        }
        Connect-PortalPrerequisiteEntra $plan 'https://app.example.com' $admin -Confirm:$false | Should -BeNullOrEmpty
        Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $AsSecureString -and $Prompt -eq 'Paste the client secret Value, then press Enter' }
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter {
            $Method -eq 'PUT' -and $Path -like '*/config/appsettings?*' -and
            $Body.properties.PORTAL_ENTRA_CLIENT_SECRET -eq 'synthetic-secret-for-prompt-test'
        }
        ($messages -join "`n") | Should -Not -Match 'synthetic-secret-for-prompt-test|not its ID; input hidden'
        Should -Invoke Export-PortalPrepareConfiguration -Times 0 -Exactly
    }

    It 'Skips credential guidance and entry when the host already stores the secret' {
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        Mock Test-PortalEntraRegistration {}
        Mock Invoke-PortalArm { return @{ properties = @{ identityProviders = @{}; PORTAL_ENTRA_CLIENT_SECRET='synthetic-stored-secret' } } }
        Mock Write-PortalStatus {}
        Mock Read-Host { throw 'Unexpected secret prompt' }
        Connect-PortalPrerequisiteEntra $plan 'https://app.example.com' $admin -Confirm:$false
        Should -Invoke Read-Host -Times 0 -Exactly
        Should -Invoke Write-PortalStatus -Times 0 -Exactly -ParameterFilter { $Message -like '*Value field*' }
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -like '*/config/appsettings?*' }
    }

    It 'Creates authenticated hosting on <Sku> with ExistingPlan=<ExistingPlan> without forcing Always On or changing networking' -ForEach @(
        @{ Sku='B1'; ExistingPlan=$false },
        @{ Sku='F1'; ExistingPlan=$true },
        @{ Sku='F1'; ExistingPlan=$false }
    ) {
        $hostingSku = $Sku
        if ($ExistingPlan) {
            $snapshot.Plan = @{ id="/subscriptions/$subscription/resourceGroups/$($inputs.ResourceGroupName)/providers/Microsoft.Web/serverfarms/$($inputs.PlanName)"
                location='centralus'; sku=@{ name='F1' }; properties=@{ reserved=$true; numberOfWorkers=1 } }
        } else {
            $inputs.PlanSku = $hostingSku
        }
        $snapshot.Providers = @(@{ namespace = 'Microsoft.ContainerRegistry'; registrationState = 'NotRegistered' })
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        $script:created = @{}
        $script:roleGrants = @()
        $script:savedSettings = @{ OTHER_SECRET = 'synthetic-only'; GALLERY_DB_PATH = '/home/data/existing.db' }
        Mock New-PortalPrerequisiteResource {
            $script:created[$ResourceId] = $Body
            if ($ResourceId -eq $plan.AppResourceId) { return @{ properties = @{ defaultHostName = 'app.example.com' }; identity = @{ principalId = $admin; type = 'SystemAssigned' } } }
            if ($ResourceId -eq $plan.RegistryId) { return @{ properties = @{ loginServer = 'registry.azurecr.io' } } }
            return @{ id = $ResourceId }
        }
        Mock Invoke-PortalPrerequisiteResource {
            return @{ id = $ResourceId; properties = @{ defaultHostName = 'app.example.com'; state = 'Stopped'; publicNetworkAccess = 'Enabled'; httpsOnly = $true } }
        }
        Mock Grant-PortalHostRole { $script:roleGrants += @{ Principal = $PrincipalId; Scope = $Scope; Role = $Role } }
        Mock Enable-PortalAlwaysOn {}
        Mock Set-PortalPlanUpgrade { throw 'Unexpected paid upgrade' }
        Mock Invoke-PortalAzJson {
            if ($Arguments -contains 'roleAssignmentMode') { return 'AbacRepositoryPermissions' }
            return @{}
        }
        Mock Invoke-PortalArm {
            if ($Path -like '*/appsettings*') {
                if ($Method -eq 'PUT') { $script:savedSettings = $Body.properties }
                return @{ properties = @{} + $script:savedSettings }
            }
            return @{ properties = @{ identityProviders = @{}; platform = @{ runtimeVersion = '~1' }
                httpSettings = @{ forwardProxy = @{ convention = 'Standard' } }; login = @{ tokenStore = @{ enabled = $false } } } }
        }
        $result = Set-PortalPrerequisites -Plan $plan -Confirm:$false
        $result.Status | Should -Be 'HostingPrepared'
        $script:created[$plan.AppResourceId].properties.siteConfig.alwaysOn | Should -Be ($hostingSku -eq 'B1')
        if (-not $ExistingPlan) {
            $script:created[$plan.PlanId].sku.name | Should -Be $hostingSku
            $script:created[$plan.PlanId].sku.tier | Should -Be $(if ($hostingSku -eq 'F1') { 'Free' } else { 'Basic' })
            $script:created[$plan.PlanId].sku.capacity | Should -Be 1
            $script:created[$plan.PlanId].properties.reserved | Should -BeTrue
        }
        Should -Invoke Set-PortalPlanUpgrade -Times 0 -Exactly
        $script:created[$plan.AppResourceId].properties.ContainsKey('publicNetworkAccess') | Should -BeFalse
        $script:created[$plan.AppResourceId].properties.ContainsKey('enabled') | Should -BeFalse
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter { $Arguments -contains 'stop' -and $Arguments -contains $plan.AppResourceId }
        $script:created[$plan.RegistryId].properties.adminUserEnabled | Should -BeFalse
        $script:created[$plan.RegistryId].properties.anonymousPullEnabled | Should -BeFalse
        $script:created[$plan.RegistryId].sku.name | Should -Be 'Basic'
        $script:created[$plan.RegistryId].properties.ContainsKey('publicNetworkAccess') | Should -BeFalse
        $script:roleGrants | Should -HaveCount 3
        $script:roleGrants[2].Role | Should -Be 'Container Registry Repository Reader'
        $script:roleGrants[2].Scope | Should -Be $plan.RegistryId
        $script:savedSettings.OTHER_SECRET | Should -Be 'synthetic-only'
        $script:savedSettings.GALLERY_DB_PATH | Should -Be '/home/data/existing.db'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $ResourceId -like '*/Microsoft.Network/*' }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'network' -or $Arguments -contains 'Microsoft.Network' -or $Arguments -contains 'vnet-integration' }
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { $Method -eq 'PATCH' -and $Body.properties.Count -eq 1 -and $Body.properties.httpsOnly -eq $true }
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -like '*/authsettingsV2*' -and $Body.properties.globalValidation.requireAuthentication -eq $true }
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -like '*/authsettingsV2*' -and
            $Body.properties.httpSettings.forwardProxy.convention -eq 'Standard' -and $Body.properties.platform.runtimeVersion -eq '~1' -and
            $Body.properties.login.tokenStore.enabled -eq $false }
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter { $Arguments -contains 'register' -and $Arguments -contains 'Microsoft.ContainerRegistry' }
        Should -Invoke Enable-PortalAlwaysOn -Times 1 -Exactly
    }

    It 'Rejects missing inputs in noninteractive mode without Azure reads' {
        Mock Invoke-PortalAzJson { throw 'Unexpected Azure request' }
        { Initialize-PortalHosting -ConfigPath (Join-Path $TestDrive 'missing-prepare.json') -NonInteractive -WhatIf } | Should -Throw '*Missing required input*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Accepts explicit PlanSku F1 in both script and function parameter binding' {
        Mock Invoke-PortalAzJson { throw 'Unexpected Azure request' }
        { Initialize-PortalHosting -PlanSku F1 -NonInteractive -WhatIf } | Should -Throw '*Missing required input*'
        { & "$PSScriptRoot/../Initialize-Portal.ps1" -PlanSku F1 -NonInteractive -WhatIf } | Should -Throw '*Missing required input*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Runs complete prerequisite planning without applying or asking for secrets' {
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        Mock Invoke-PortalPrerequisiteResource { throw 'Unexpected mutation' }
        Mock Invoke-PortalArm { throw 'Unexpected mutation' }
        Mock Read-Host { throw 'Unexpected secret or confirmation prompt' }
        $result = Initialize-PortalHosting @inputs -EntraMode Create -EntraDisplayName 'Company Portal' -NonInteractive -WhatIf
        $result.Status | Should -Be 'PreviewOnly'
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
        Should -Invoke Read-Host -Times 0 -Exactly
    }



    It 'Does not deploy an older image after its new build is cancelled' {
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        Mock Set-PortalPrerequisites { return @{ Status = 'HostingPrepared'; Origin = 'https://app.example.com'; LoginServer = 'registry.azurecr.io' } }
        Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Existing'; EntraClientId=$tenant } }
        Mock Get-PortalEntraReadiness { return @{ Status='Ready'; Checks=@() } }
        Mock Connect-PortalPrerequisiteEntra {}
        Mock Read-Host {
            if ($Prompt -like 'Next:*') { return 'B' }
            if ($Prompt -like 'Image repository*') { return 'deployment-portal:release-1' }
            return ''
        }
        Mock Publish-PortalPrerequisiteImage { return @{ Status = 'Cancelled' } }
        Mock Invoke-PortalDeployment { throw 'Unexpected deployment' }
        (Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false).Status | Should -Be 'Cancelled'
        Should -Invoke Publish-PortalPrerequisiteImage -Times 1 -Exactly
        Should -Invoke Invoke-PortalDeployment -Times 0 -Exactly
    }

    Context 'Existing Entra on a newly prepared host' {
        BeforeEach {
            $script:preparedHost = $false
            $script:repairedEntra = $false
            $script:postHostCalls = [System.Collections.Generic.List[string]]::new()
            $script:preparedReadiness = 'RequiresRepair'
            Mock Write-Host {}
            Mock Write-Warning {}
            Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Existing'; EntraClientId=$tenant } }
            Mock Get-PortalPrerequisiteSnapshot {
                if ($script:preparedHost) { return @{ Site=@{ properties=@{ defaultHostName='actual.example.com' } } } }
                return $snapshot
            }
            Mock Get-PortalEntraReadiness {
                if (-not $script:preparedHost) { return @{ Status='PendingHost'; Checks=@() } }
                $script:postHostCalls.Add('Validate')
                return @{ Status=$(if ($script:repairedEntra) { 'Ready' } else { $script:preparedReadiness });
                    Origin='https://actual.example.com'; ClientId=$tenant; Checks=@() }
            }
            Mock Set-PortalPrerequisites {
                $script:preparedHost=$true
                $script:postHostCalls.Add('Host')
                return @{ Status='HostingPrepared'; Origin='https://actual.example.com'; LoginServer='registry.azurecr.io' }
            }
            Mock Read-Host {
                if ($Prompt -like 'Plan the listed Entra repairs for the prepared host?*') { return 'Yes' }
                if ($Prompt -like 'Next:*') { return '' }
                throw "Unexpected prompt: $Prompt"
            }
            Mock Repair-PortalEntraReadiness {
                $script:postHostCalls.Add('Repair')
                $script:repairedEntra=$true
                return $true
            }
            Mock Connect-PortalPrerequisiteEntra { $script:postHostCalls.Add('Connect') }
            Mock Invoke-PortalDeployment { throw 'Unexpected image deployment' }
            Mock Publish-PortalPrerequisiteImage { throw 'Unexpected image publication' }
        }

        It 'Rechecks the new hostname and repairs before connecting' {
            $result=Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false
            $result.Status | Should -Be 'HostingPrepared'
            ($script:postHostCalls -join ',') | Should -Be 'Host,Validate,Repair,Validate,Connect,Validate'
            Should -Invoke Repair-PortalEntraReadiness -Times 1 -Exactly -ParameterFilter { $Snapshot.Site.properties.defaultHostName -eq 'actual.example.com' -and $Readiness.Origin -eq 'https://actual.example.com' }
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 1 -Exactly -ParameterFilter { $Origin -eq 'https://actual.example.com' }
        }

        It 'Leaves hosting intact when repair planning is declined' {
            Mock Read-Host { return 'No' }
            { Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false } | Should -Throw '*Hosting is retained*'
            Should -Invoke Repair-PortalEntraReadiness -Times 0 -Exactly
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
            Should -Invoke Invoke-PortalDeployment -Times 0 -Exactly
        }

        It 'Stops when separate repair confirmation is declined' {
            Mock Repair-PortalEntraReadiness { return $false }
            (Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false).Status | Should -Be 'HostingPrepared'
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
        }

        It 'Stops for unverified directory findings: <_>' -ForEach @('Blocked','PendingHost') {
            $script:preparedReadiness=$_
            { Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false } | Should -Throw '*readiness is not verified*'
            Should -Invoke Repair-PortalEntraReadiness -Times 0 -Exactly
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
        }

        It 'Requires explicit repair approval in unattended mode' {
            { Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -NonInteractive -Confirm:$false } | Should -Throw '*not approved*'
            Should -Invoke Read-Host -Times 0 -Exactly
            Should -Invoke Repair-PortalEntraReadiness -Times 0 -Exactly
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
        }

        It 'Uses RepairEntra for unattended repair without prompting' {
            (Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -RepairEntra -NonInteractive -Confirm:$false).Status | Should -Be 'HostingPrepared'
            Should -Invoke Read-Host -Times 0 -Exactly
            Should -Invoke Repair-PortalEntraReadiness -Times 1 -Exactly
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 1 -Exactly
        }

        It 'Does not connect if repair readback remains unready' {
            Mock Repair-PortalEntraReadiness { return $true }
            { Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false } | Should -Throw '*readiness is not verified*'
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
        }

        It 'Keeps PendingHost in WhatIf without provisioning or repair' {
            $result=Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -WhatIf
            $result.EntraReadiness | Should -Be 'PendingHost'
            Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
            Should -Invoke Repair-PortalEntraReadiness -Times 0 -Exactly
            Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
        }
    }

    Context 'Image handoff' {
        BeforeEach {
            Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
            Mock Set-PortalPrerequisites { return @{ Status='HostingPrepared'; Origin='https://app.example.com'; LoginServer='registry.azurecr.io' } }
            Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Existing'; EntraClientId=$tenant } }
            Mock Get-PortalEntraReadiness { return @{ Status='Ready'; Checks=@() } }
            Mock Connect-PortalPrerequisiteEntra {}
            Mock Write-Warning {}
            Mock Publish-PortalPrerequisiteImage { return @{ Status='ImagePublished' } }
            Mock Invoke-PortalDeployment { return @{ Status='ConfigurationApplied' } }
            $script:imagePromptCount = 0
        }

        It 'Retries invalid input before pip or deployment in mode <_>' -ForEach @('B','D') {
            $nextAction = $_
            $selectedImage = if ($nextAction -eq 'B') { 'deployment-portal:latest' } else { 'deployment-portal@sha256:' + ('a' * 64) }
            $script:imageAnswers = @('latest', '', 'portal;bad:tag', " $selectedImage ")
            Mock Read-Host {
                if ($Prompt -like 'Next:*') { return $nextAction }
                if ($Prompt -like 'Image repository*') {
                    if ($script:imagePromptCount -ge $script:imageAnswers.Count) { throw 'Unexpected repeated image prompt' }
                    $answer = $script:imageAnswers[$script:imagePromptCount]
                    $script:imagePromptCount++
                    return $answer
                }
                if ($Prompt -like 'Approved pip configuration*' -and $nextAction -eq 'B') {
                    if ($script:imagePromptCount -ne 4) { throw 'Pip prompt before valid image' }
                    return ''
                }
                throw "Unexpected prompt: $Prompt"
            }
            (Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false).Status | Should -Be 'ConfigurationApplied'
            $script:imagePromptCount | Should -Be 4
            Should -Invoke Write-Warning -Times 3 -Exactly -ParameterFilter { $Message -like '*tag*' }
            Should -Invoke Invoke-PortalDeployment -Times 1 -Exactly -ParameterFilter { $Image -eq $selectedImage }
            if ($nextAction -eq 'B') {
                Should -Invoke Publish-PortalPrerequisiteImage -Times 1 -Exactly -ParameterFilter { $ImageReference -eq $selectedImage -and $LoginServer -eq 'registry.azurecr.io' }
                Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $Prompt -like 'Approved pip configuration*' }
            } else {
                Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
                Should -Invoke Read-Host -Times 0 -Exactly -ParameterFilter { $Prompt -like 'Approved pip configuration*' }
            }
        }

        It 'Keeps hosting prepared when image selection is cancelled in mode <_>' -ForEach @('B','D') {
            $nextAction = $_
            Mock Read-Host {
                if ($Prompt -like 'Next:*') { return $nextAction }
                if ($Prompt -like 'Image repository*') {
                    $script:imagePromptCount++
                    if ($script:imagePromptCount -eq 1) { return 'latest' }
                    if ($script:imagePromptCount -eq 2) { return ' q ' }
                    throw 'Unexpected repeated image prompt'
                }
                throw "Unexpected prompt after cancellation: $Prompt"
            }
            (Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false).Status | Should -Be 'HostingPrepared'
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Invoke-PortalDeployment -Times 0 -Exactly
            Should -Invoke Read-Host -Times 0 -Exactly -ParameterFilter { $Prompt -like 'Approved pip configuration*' }
        }

        It 'Stops after successful hosting preparation when Next is left blank' {
            Mock Read-Host {
                if ($Prompt -like 'Next:*') { return '' }
                throw 'Unexpected image prompt'
            }
            (Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false).Status | Should -Be 'HostingPrepared'
            Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $Prompt -like '*hosting prepared; deploy later*' }
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Invoke-PortalDeployment -Times 0 -Exactly
        }

        It 'Offers <Choice> after successful Entra checks without granting or verifying consent' -ForEach @(
            @{ Choice='Stop'; NextPrompts=0 }, @{ Choice='Continue'; NextPrompts=1 }
        ) {
            $checkpointChoice=$Choice
            Mock Get-PortalEntraReadiness { return @{ Status='Ready'; ClientId=$tenant; Origin='https://app.example.com'; Checks=@(
                @{ Check='Employee access'; Status='Ready'; Detail='Assigned' }) } }
            Mock Read-Host {
                if ($Prompt -like 'Admin consent checkpoint*') { return $checkpointChoice }
                if ($Prompt -like 'Next:*') { return '' }
                throw "Unexpected consent interaction: $Prompt"
            }
            Mock Invoke-PortalFoundationGraph { throw 'Unexpected directory change' }
            $result=Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -Confirm:$false
            $result.Status | Should -Be 'HostingPrepared'
            Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $Prompt -like 'Admin consent checkpoint*' }
            Should -Invoke Read-Host -Times $NextPrompts -Exactly -ParameterFilter { $Prompt -like 'Next:*' }
            Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly
            Should -Invoke Publish-PortalPrerequisiteImage -Times 0 -Exactly
            Should -Invoke Invoke-PortalDeployment -Times 0 -Exactly
        }

        It 'Does not add a consent prompt in <Mode> mode' -ForEach @('Preview','Unattended') {
            Mock Get-PortalEntraReadiness { return @{ Status='Ready'; Checks=@(@{ Check='Employee access'; Status='Ready'; Detail='Assigned' }) } }
            Mock Read-Host { throw 'Unexpected consent prompt' }
            if ($Mode -eq 'Preview') {
                $result=Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -WhatIf
                $result.SignInVerified | Should -BeFalse
                Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
            } else {
                $result=Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -NonInteractive -Confirm:$false
                $result.Status | Should -Be 'HostingPrepared'
            }
            Should -Invoke Read-Host -Times 0 -Exactly
        }
    }

    It 'Preserves authentication options when connecting an existing organization registration' {
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        Mock Test-PortalEntraRegistration {}
        Mock Read-Host { throw 'Unexpected secret prompt' }
        Mock Invoke-PortalArm {
            if ($Path -like '*/appsettings*') { return @{ properties = @{ PORTAL_ENTRA_CLIENT_SECRET = 'synthetic-only' } } }
            return @{ properties = @{ identityProviders = @{}; platform = @{ runtimeVersion = '~1' }
                httpSettings = @{ forwardProxy = @{ convention = 'Standard' } }; login = @{ tokenStore = @{ enabled = $false } } } }
        }
        Connect-PortalPrerequisiteEntra $plan 'https://app.example.com' $admin -NonInteractive -Confirm:$false
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Path -like '*/authsettingsV2*' -and
            $Body.properties.httpSettings.forwardProxy.convention -eq 'Standard' -and $Body.properties.platform.runtimeVersion -eq '~1' -and
            $Body.properties.login.tokenStore.enabled -eq $false -and $Body.properties.identityProviders.azureActiveDirectory.registration.clientId -eq $admin }
        Should -Invoke Read-Host -Times 0 -Exactly
    }

    It 'Preserves private organization network configuration during planning' {
        $snapshot.Site = @{ id = "/subscriptions/$subscription/resourceGroups/$($inputs.ResourceGroupName)/providers/Microsoft.Web/sites/$($inputs.AppName)"
            location = 'centralus'; kind = 'app,linux'; properties = @{ publicNetworkAccess = 'Disabled'; virtualNetworkSubnetId = '/organization/subnet'
                serverFarmId = "/subscriptions/$subscription/resourceGroups/$($inputs.ResourceGroupName)/providers/Microsoft.Web/serverfarms/$($inputs.PlanName)" } }
        $before = $snapshot | ConvertTo-Json -Depth 30
        $null = Get-PortalPrerequisitePlan $inputs $snapshot
        ($snapshot | ConvertTo-Json -Depth 30) | Should -Be $before
    }

    It 'Retains existing plan and registry IDs in other groups instead of recreating them locally' {
        $inputs.PlanResourceId = "/subscriptions/$subscription/resourceGroups/shared-plan/providers/Microsoft.Web/serverfarms/$($inputs.PlanName)"
        $inputs.RegistryResourceId = "/subscriptions/$subscription/resourceGroups/shared-registry/providers/Microsoft.ContainerRegistry/registries/$($inputs.RegistryName)"
        $snapshot.Plan = @{ id=$inputs.PlanResourceId; location='centralus'; properties=@{ reserved=$true; numberOfWorkers=1 } }
        $snapshot.Registry = @{ id=$inputs.RegistryResourceId; location='eastus'; properties=@{ adminUserEnabled=$false } }
        $plan = Get-PortalPrerequisitePlan $inputs $snapshot
        $plan.PlanId | Should -Be $inputs.PlanResourceId
        $plan.RegistryId | Should -Be $inputs.RegistryResourceId
        $inputs.PlanResourceId = $inputs.PlanResourceId.Replace($subscription,$tenant)
        { Get-PortalPrerequisitePlan $inputs $snapshot } | Should -Throw '*deployment subscription*'
    }

    It 'Does not create a selected existing resource that disappeared' {
        Mock Invoke-PortalPrerequisiteResource { return $null }
        { New-PortalPrerequisiteResource $subscription '/selected' '2024-11-01' @{} -RequireExisting } | Should -Throw '*not be recreated*'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' }
    }

    It 'Rejects network ARM mutations before authorization' {
        Mock Invoke-PortalAzJson { throw 'Unexpected authorization' }
        { Invoke-PortalPrerequisiteResource $subscription "/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Network/virtualNetworks/test" '2024-05-01' -Method PUT -Body @{} } | Should -Throw '*destination*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly
    }

    It 'Uses discovered hosting values and only prompts for remaining credential lifetime' {
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        Mock Invoke-PortalPrerequisiteResource { throw 'Unexpected mutation' }
        Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Create'; EntraDisplayName='Company Portal' } }
        Mock Read-Host {
            if ($Prompt -like 'Generated credential lifetime*') { return '90' }
            throw "Unexpected prompt: $Prompt"
        }
        (Initialize-PortalHosting @inputs -WhatIf).Status | Should -Be 'PreviewOnly'
        Should -Invoke Read-Host -Times 1 -Exactly
        Should -Invoke Get-PortalDiscoveredInputs -Times 1 -Exactly
        Should -Invoke Read-Host -Times 0 -Exactly -ParameterFilter { $AsSecureString -or $Prompt -match 'VPN|subnet|DNS|network' }
    }

    It 'Allows F1 preview and apply without upgrade approval when Entra is ready' {
        $snapshot.Plan = @{ id="/subscriptions/$subscription/resourceGroups/$($inputs.ResourceGroupName)/providers/Microsoft.Web/serverfarms/$($inputs.PlanName)"
            location='centralus'; sku=@{ name='F1' }; properties=@{ reserved=$true; numberOfWorkers=1 } }
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        Mock Get-PortalEntraReadiness { return @{ Status='Ready'; Checks=@() } }
        Mock Set-PortalPrerequisites { return @{ Status='HostingPrepared'; Origin='https://app.example.com'; LoginServer='registry.azurecr.io' } }
        Mock Connect-PortalPrerequisiteEntra {}
        Mock Read-Host { throw 'Unexpected upgrade or secret prompt' }
        $preview = Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -NonInteractive -WhatIf
        $preview.HostingReadiness | Should -Be 'Warning'
        $preview.ReadyForApply | Should -BeTrue
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
        (Initialize-PortalHosting @inputs -EntraMode Existing -EntraClientId $tenant -NonInteractive -Confirm:$false).Status | Should -Be 'HostingPrepared'
        Should -Invoke Set-PortalPrerequisites -Times 1 -Exactly -ParameterFilter { -not $Plan.HostingReadiness.UpgradeRequired -and $Plan.HostingReadiness.TargetSku -eq 'F1' }
        Should -Invoke Read-Host -Times 0 -Exactly
    }

    It 'Reports the directory HTTP <_> blocker on F1 in preview and the final exception' -ForEach @(401,403) {
        $failureStatus = $_
        $snapshot.Plan = @{ id="/subscriptions/$subscription/resourceGroups/$($inputs.ResourceGroupName)/providers/Microsoft.Web/serverfarms/$($inputs.PlanName)"
            location='centralus'; sku=@{ name='F1' }; properties=@{ reserved=$true; numberOfWorkers=1 } }
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Existing'; EntraClientId=$tenant } }
        Mock Get-PortalEntraReadiness { return @{ Status='Blocked'; Checks=@(@{ Check='Directory registration read'; Status='Blocked'; Detail="HTTP $failureStatus" }) } }
        Mock Set-PortalPrerequisites { throw 'Unexpected preparation writes' }
        $preview = Initialize-PortalHosting @inputs -WhatIf
        $preview.ReadyForApply | Should -BeFalse
        $preview.HostingReadiness | Should -Be 'Warning'
        $preview.EntraReadiness | Should -Be 'Blocked'
        $preview.BlockingFindings | Should -HaveCount 1
        $preview.BlockingFindings[0].Check | Should -Be 'Directory registration read'
        $preview.BlockingFindings[0].Detail | Should -Be "HTTP $failureStatus"
        { Initialize-PortalHosting @inputs -Confirm:$false } | Should -Throw "*Directory registration read: HTTP $failureStatus*No preparation writes*"
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
    }

    It 'Explains declined Entra repairs without including unrelated registration data' {
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Existing'; EntraClientId=$tenant } }
        Mock Get-PortalEntraReadiness { return @{ Status='RequiresRepair'; Application=@{ secret='synthetic-private-data' }; Checks=@(
            @{ Check='Entra callback'; Status='Ready'; Detail='Already correct' },
            @{ Check='Entra ID tokens'; Status='Repairable'; Detail='Enable ID-token issuance.' }) } }
        Mock Read-PortalChoice { return 'No' }
        Mock Set-PortalPrerequisites { throw 'Unexpected preparation write' }
        Mock Repair-PortalEntraReadiness { throw 'Unexpected directory write' }
        $preview = Initialize-PortalHosting @inputs -WhatIf
        $preview.ReadyForApply | Should -BeFalse
        $preview.BlockingFindings | Should -HaveCount 2
        $preview.EntraChecks | Should -HaveCount 2
        $preview.EntraChecks[0].Status | Should -Be 'Ready'
        ($preview | ConvertTo-Json -Depth 20) | Should -Not -Match 'synthetic-private-data'
        ($preview.BlockingFindings | ConvertTo-Json) | Should -Not -Match 'Already correct|synthetic-private-data'
        { Initialize-PortalHosting @inputs -Confirm:$false } | Should -Throw '*Entra repair approval: Required Entra repairs were not approved*Entra ID tokens: Enable ID-token issuance.*No preparation writes*'
        Should -Invoke Set-PortalPrerequisites -Times 0 -Exactly
        Should -Invoke Repair-PortalEntraReadiness -Times 0 -Exactly
    }

    It 'Includes approved Entra repairs in preview without performing them' {
        Mock Get-PortalPrerequisiteSnapshot { return $snapshot }
        Mock Get-PortalDiscoveredInputs { return @{ Inputs=$inputs; EntraMode='Existing'; EntraClientId=$tenant } }
        Mock Get-PortalEntraReadiness { return @{ Status='RequiresRepair'; Checks=@() } }
        Mock Repair-PortalEntraReadiness { throw 'Unexpected directory write' }
        $preview=Initialize-PortalHosting @inputs -RepairEntra -WhatIf
        $preview.ReadyForApply | Should -BeTrue
        $preview.EntraRepairPlanned | Should -BeTrue
        $preview.BlockingFindings | Should -HaveCount 0
        Should -Invoke Repair-PortalEntraReadiness -Times 0 -Exactly
    }

    It 'Discovers hosting without any network resource or provider reads' {
        Mock Invoke-PortalPrerequisiteResource { return $null }
        Mock Invoke-PortalAzJson { return @{} }
        $null = Get-PortalPrerequisiteSnapshot $inputs
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 4 -Exactly
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $ResourceId -like '*/Microsoft.Network/*' }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'Microsoft.Network' }
    }
}

Describe 'Discovery choices' {
    It 'Accepts Enter for a visible existing default' {
        Mock Read-Host { return '' }
        Read-PortalChoice 'Subscription' @(@{ Value=$subscription; Label='Organization subscription [default]' }) $subscription | Should -Be $subscription
    }

    It 'Retries invalid menu entries and accepts a numbered choice' {
        $script:choiceCount = 0
        Mock Read-Host { $script:choiceCount++; if ($script:choiceCount -eq 1) { return '99' }; return '1' }
        Read-PortalChoice 'Subscription' @(@{ Value=$subscription; Label='Organization subscription' }) | Should -Be $subscription
        Should -Invoke Read-Host -Times 2 -Exactly
    }

    It 'Offers a new registry name instead of aborting on blank input' {
        Mock Read-Host { return '' }
        Read-PortalChoice 'Registry' -AllowNew -NewDefault 'crportaltest001' | Should -Be 'crportaltest001'
    }

    It 'Normalizes a region display name without guessing' {
        ConvertTo-PortalLocation ' Central US ' @(@{ name='centralus'; displayName='Central US' }) | Should -Be 'centralus'
        { ConvertTo-PortalLocation 'invalid' @(@{ name='centralus'; displayName='Central US' }) } | Should -Throw '*Azure region*'
    }
}

Describe 'Azure-guided prerequisite discovery' {
    BeforeEach {
        $groupName = 'rg-portal-test-centralus-001'
        $groupId = "/subscriptions/$subscription/resourceGroups/$groupName"
        $appId = "$groupId/providers/Microsoft.Web/sites/app-portal-test-centralus-001"
        $planId = "$groupId/providers/Microsoft.Web/serverfarms/asp-portal-test-centralus-001"
        $script:discoveryAppId = $appId
        $script:discoveryPlanId = $planId
        $values = @{ PlanSku='B1'; PlanSkuProvided=$false }
        $script:discoveryCalls = @()
        Mock Invoke-PortalAzJson {
            $script:discoveryCalls += ,$Arguments
            switch ($Arguments[0] + ' ' + $Arguments[1]) {
                'account show' { return @{ id=$subscription; tenantId=$tenant; name='Organization Sandbox'; state='Enabled'; environmentName='AzureCloud'; user=@{ name='operator@example.com'; type='user' } } }
                'account list' { return @(@{ id=$subscription; tenantId=$tenant; name='Organization Sandbox'; isDefault=$true; state='Enabled'; cloudName='AzureCloud'; user=@{ name='operator@example.com'; type='user' } }) }
                'group list' { return @(@{ id=$groupId; name=$groupName; location='centralus' }) }
                'resource list' { return @(@{ id=$appId; name='app-portal-test-centralus-001'; type='Microsoft.Web/sites'; kind='app,linux'; location='centralus' },
                    @{ id=$planId; name='asp-portal-test-centralus-001'; type='Microsoft.Web/serverfarms'; kind='linux'; location='centralus' }) }
                'account list-locations' { return @(@{ name='centralus'; displayName='Central US' }, @{ name='eastus'; displayName='East US' }) }
                'acr check-name' { return @{ nameAvailable=$true } }
                default { throw "Unexpected Azure command: $($Arguments -join ' ')" }
            }
        }
        Mock Invoke-PortalPrerequisiteResource {
            if ($ResourceId -like '/subscriptions/*/locations') { return @{ value=@(@{ name='centralus'; displayName='Central US' }, @{ name='eastus'; displayName='East US' }) } }
            if ($ResourceId -eq $script:discoveryAppId) { return @{ id=$script:discoveryAppId; kind='app,linux'; location='centralus'; properties=@{ serverFarmId=$script:discoveryPlanId; defaultHostName='portal.azurewebsites.net' } } }
            if ($ResourceId -eq $script:discoveryPlanId) { return @{ id=$script:discoveryPlanId; location='centralus'; sku=@{ name='B1' } } }
            return $null
        }
        Mock Invoke-PortalArm {
            if ($Path -like '*/authsettingsV2?*') { return @{ properties=@{ identityProviders=@{ azureActiveDirectory=@{ registration=@{ clientId=$tenant } } } } } }
            if ($Path -like '*/appsettings/*') { return @{ properties=@{ GALLERY_ADMIN_OIDS=$admin; PRIVATE_SECRET='synthetic-must-not-return' } } }
            return @{ properties=@{} }
        }
        Mock Invoke-PortalFoundationGraph { throw 'Configured Entra/admin IDs should not need directory discovery' }
        Mock Read-Host { return '' }
    }

    It 'Reuses default subscription, existing group/app/plan/Entra and suggests a missing registry' {
        $result = Get-PortalDiscoveredInputs $values
        $result.Inputs.SubscriptionId | Should -Be $subscription
        $result.Inputs.ResourceGroupName | Should -Be $groupName
        $result.Inputs.Location | Should -Be 'centralus'
        $result.Inputs.PlanName | Should -Be 'asp-portal-test-centralus-001'
        $result.Inputs.RegistryName | Should -Match '^crportaltestcentralus001[a-f0-9]{6}$'
        $result.Inputs.AdminObjectIds | Should -Contain $admin
        $result.EntraMode | Should -Be 'Existing'
        $result.EntraClientId | Should -Be $tenant
        ($result | ConvertTo-Json -Depth 15) | Should -Not -Match 'synthetic-must-not-return'
        Should -Invoke Read-Host -Times 0 -Exactly -ParameterFilter { $AsSecureString -or $Prompt -match 'region|plan SKU|administrator' }
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH') }
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' }
        @($script:discoveryCalls | Where-Object { $_[0] -ne 'account' -or $_[1] -notin @('list','show') } | Where-Object { $_ -notcontains $subscription }).Count | Should -Be 0
    }

    It 'Accepts a passed existing group and normalizes Central US without reasking' {
        $values.SubscriptionId=$subscription; $values.ResourceGroupName=$groupName; $values.Location='Central US'
        $result = Get-PortalDiscoveredInputs $values
        $result.Inputs.Location | Should -Be 'centralus'
        Should -Invoke Read-Host -Times 0 -Exactly -ParameterFilter { $Prompt -like 'Subscription*' -or $Prompt -like 'Resource group*' -or $Prompt -like 'Azure region*' }
    }

    It 'Plans new services with SKU selection <_> without writes' -ForEach @('', 'F1', '6') {
        $skuSelection = $_
        $values.ResourceGroupName='rg-new-portal'; $values.AdminObjectIds=@($admin)
        Mock Invoke-PortalAzJson { return @() } -ParameterFilter { $Arguments[0] -eq 'resource' }
        Mock Invoke-PortalPrerequisiteResource {
            if ($ResourceId -like '/subscriptions/*/locations') { return @{ value=@(@{ name='centralus'; displayName='Central US' }) } }
            return $null
        }
        Mock Invoke-PortalFoundationGraph { return @{ value=@() } }
        Mock Read-Host {
            if ($Prompt -like 'Azure region*') { return 'Central US' }
            if ($Prompt -like 'New Linux plan SKU*') { return $skuSelection }
            return ''
        }
        $result = Get-PortalDiscoveredInputs $values
        $result.Inputs.PlanSku | Should -Be $(if ($skuSelection) { 'F1' } else { 'B1' })
        Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $Prompt -like 'New Linux plan SKU*' }
        $result.Inputs.AppName | Should -Be 'app-new-portal'
        $result.Inputs.PlanName | Should -Be 'asp-new-portal'
        $result.Inputs.Location | Should -Be 'centralus'
        $result.EntraMode | Should -Be 'Create'
        $result.EntraDisplayName | Should -Be 'app-new-portal'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'create' -or $Arguments -contains 'set' -or $Arguments -contains 'register' }
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -eq 'POST' }
    }

    It 'Stops on denied discovery rather than treating the resource group as absent' {
        Mock Invoke-PortalAzJson { throw 'Discovery permission denied' } -ParameterFilter { $Arguments[0] -eq 'group' }
        { Get-PortalDiscoveredInputs $values } | Should -Throw '*permission denied*'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly
    }

    It 'Rejects <Case> current account metadata before group discovery' -ForEach @(
        @{ Case='disabled'; State='Disabled'; CloudName='AzureCloud' }
        @{ Case='non-public cloud'; State='Enabled'; CloudName='AzureUSGovernment' }
        @{ Case='missing cloud'; State='Enabled'; CloudName=$null }
    ) {
        Mock Invoke-PortalAzJson {
            return @{ id=$subscription; tenantId=$tenant; state=$State; environmentName=$CloudName; user=@{ name='operator@example.com'; type='user' } }
        } -ParameterFilter { $Arguments[0] -eq 'account' -and $Arguments[1] -eq 'show' }
        { Get-PortalDiscoveredInputs $values } | Should -Throw '*current Azure CLI account*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments[0] -eq 'group' }
        Should -Invoke Read-Host -Times 0 -Exactly
    }

    It 'Uses the active account when the cache default belongs to another login' {
        $script:activeLoginPrompts = @()
        Mock Read-Host { $script:activeLoginPrompts += $Prompt; return '' }
        Mock Invoke-PortalAzJson {
            return @(@{ id=$admin; tenantId=$tenant; name='Other cached login'; state='Enabled'; cloudName='AzureCloud'; isDefault=$true; user=@{ name='other@example.com'; type='user' } })
        } -ParameterFilter { $Arguments[0] -eq 'account' -and $Arguments[1] -eq 'list' }
        (Get-PortalDiscoveredInputs $values).Inputs.SubscriptionId | Should -Be $subscription
        ($script:activeLoginPrompts -join "`n") | Should -Match 'Subscription \[Enter = option 1; Q = cancel\]'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains $admin -or $Arguments -contains 'set' -or $Arguments -contains 'login' }
    }

    It 'Refuses explicitly selecting a different cached <Case>' -ForEach @(
        @{ Case='user'; OtherName='other@example.com'; OtherTenant='22222222-2222-4222-8222-222222222222'; OtherType='user' }
        @{ Case='tenant'; OtherName='operator@example.com'; OtherTenant='44444444-4444-4444-8444-444444444444'; OtherType='user' }
        @{ Case='identity type'; OtherName='operator@example.com'; OtherTenant='22222222-2222-4222-8222-222222222222'; OtherType='servicePrincipal' }
    ) {
        $values.SubscriptionId=$admin
        Mock Invoke-PortalAzJson {
            return @(@{ id=$admin; tenantId=$OtherTenant; name='Other context'; state='Enabled'; cloudName='AzureCloud'; isDefault=$true; user=@{ name=$OtherName; type=$OtherType } })
        } -ParameterFilter { $Arguments[0] -eq 'account' -and $Arguments[1] -eq 'list' }
        { Get-PortalDiscoveredInputs $values } | Should -Throw '*current Azure CLI identity and tenant*'
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments[0] -eq 'group' }
    }

    It 'Rechecks the account for another subscription under the same login' -ForEach @($false,$true) {
        $changedIdentity = $_
        $values.SubscriptionId=$admin
        Mock Invoke-PortalAzJson {
            return @(@{ id=$admin; tenantId=$tenant; name='Second subscription'; state='Enabled'; cloudName='AzureCloud'; user=@{ name='operator@example.com'; type='user' } })
        } -ParameterFilter { $Arguments[0] -eq 'account' -and $Arguments[1] -eq 'list' }
        Mock Invoke-PortalAzJson {
            return @{ id=$admin; tenantId=$tenant; state='Enabled'; environmentName='AzureCloud'; user=@{ name=$(if($changedIdentity){'other@example.com'}else{'operator@example.com'}); type='user' } }
        } -ParameterFilter { $Arguments[0] -eq 'account' -and $Arguments[1] -eq 'show' -and $Arguments -contains '--subscription' }
        Mock Invoke-PortalAzJson { throw 'Reached intended subscription group discovery' } -ParameterFilter { $Arguments[0] -eq 'group' }
        if ($changedIdentity) {
            { Get-PortalDiscoveredInputs $values } | Should -Throw '*different CLI identity*'
            Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments[0] -eq 'group' }
        } else {
            { Get-PortalDiscoveredInputs $values } | Should -Throw '*Reached intended subscription*'
            Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter { $Arguments[0] -eq 'group' -and $Arguments -contains $admin }
        }
        Should -Invoke Invoke-PortalAzJson -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'set' -or $Arguments -contains 'login' }
    }

    It 'Offers a separate app and registration when reuse is declined without replacing identity' {
        Mock Read-Host { if ($Prompt -like 'Reuse configured Entra*') { return 'No' }; return '' }
        $result = Get-PortalDiscoveredInputs $values
        $result.Inputs.AppName | Should -Be 'app-portal-test-centralus-001-2'
        $result.EntraMode | Should -Be 'Create'
        $result.EntraClientId | Should -BeNullOrEmpty
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -eq 'POST' }
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly -ParameterFilter { $Method -eq 'PUT' }
    }

    It 'Offers matching tenant registrations when the selected app has no provider' {
        Mock Invoke-PortalArm { return @{ properties=@{} } }
        $values.AdminObjectIds=@($admin)
        Mock Invoke-PortalFoundationGraph { return @{ value=@(@{ appId=$tenant; displayName='app-portal-test-centralus-001'; signInAudience='AzureADMyOrg' }) } }
        $result = Get-PortalDiscoveredInputs $values
        $result.EntraMode | Should -Be 'Existing'
        $result.EntraClientId | Should -Be $tenant
        Should -Invoke Invoke-PortalFoundationGraph -Times 1 -Exactly -ParameterFilter { $Path -like '/applications?*' -and $TenantId -eq $tenant }
    }

    It 'Uses the operator only as a confirmed administrator suggestion when none is configured' {
        Mock Invoke-PortalArm {
            if ($Path -like '*/authsettingsV2?*') { return @{ properties=@{ identityProviders=@{ azureActiveDirectory=@{ registration=@{ clientId=$tenant } } } } } }
            return @{ properties=@{} }
        }
        Mock Invoke-PortalFoundationGraph { return @{ id=$admin; displayName='Test Operator'; userType='Member'; accountEnabled=$true } }
        (Get-PortalDiscoveredInputs $values).Inputs.AdminObjectIds | Should -Contain $admin
        Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $Prompt -like 'Initial administrator user object IDs*' }
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -eq 'POST' }
    }

    It 'Finds an existing app by typed name even when a new group was selected' {
        $values.ResourceGroupName='rg-new-portal'; $values.AppName='app-portal-test-centralus-001'
        $result = Get-PortalDiscoveredInputs $values
        $result.Inputs.ResourceGroupName | Should -Be $groupName
        $result.Inputs.PlanResourceId | Should -Be $script:discoveryPlanId
        Should -Invoke Invoke-PortalAzJson -Times 1 -Exactly -ParameterFilter { $Arguments[0] -eq 'resource' -and $Arguments -notcontains '--resource-group' }
        Should -Invoke Read-Host -Times 1 -Exactly -ParameterFilter { $Prompt.StartsWith('Use existing app in its original') }
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH') }
    }

    It 'Disambiguates duplicate plan names by full ID and group' {
        $otherPlanId = "/subscriptions/$subscription/resourceGroups/other/providers/Microsoft.Web/serverfarms/shared"
        $resources = @(@{ id="$groupId/providers/Microsoft.Web/serverfarms/shared"; name='shared'; location='centralus' },
            @{ id=$otherPlanId; name='shared'; location='centralus' })
        Mock Read-Host { return $otherPlanId }
        $selected = Read-PortalResourceChoice 'Plan' $resources -Name 'shared' -PreferredGroup $groupName
        $selected.Group | Should -Be 'other'
        $selected.Id | Should -Be $resources[1].id
    }
}

Describe 'Preparation command entry point' {
    BeforeAll {
        function az { param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments) throw 'Unexpected unmocked Azure CLI call' }
    }

    It 'Runs Prepare WhatIf choosing <_> and Entra repairs without writes' -ForEach @('Keep','Upgrade') {
        $upgradeChoice = $_
        $script:entryPromptCount = 0
        Mock Read-Host {
            $script:entryPromptCount++
            if ($script:entryPromptCount -gt 14) { throw "Unexpected repeated entry-point prompt: $Prompt" }
            if ($Prompt.StartsWith('Optional Always On upgrade')) { return $(if ($upgradeChoice -eq 'Upgrade') { 'Upgrade' } else { '' }) }
            if ($Prompt.StartsWith('Plan the listed Entra repairs?')) { return 'Yes' }
            return ''
        }
        Mock az {
            $fixtureSubscription='11111111-1111-4111-8111-111111111111'
            $fixtureTenant='22222222-2222-4222-8222-222222222222'
            $fixtureGroup="/subscriptions/$fixtureSubscription/resourceGroups/rg-portal-test"
            $global:LASTEXITCODE = 0
            $data = switch ($Arguments[0] + ' ' + $Arguments[1]) {
                'account list' { ,@(@{ id=$fixtureSubscription; tenantId=$fixtureTenant; name='Organization Sandbox'; isDefault=$true; state='Enabled'; cloudName='AzureCloud'; user=@{ name='operator@example.com'; type='user' } }) }
                'account show' { @{ id=$fixtureSubscription; tenantId=$fixtureTenant; name='Organization Sandbox'; state='Enabled'; environmentName='AzureCloud'; user=@{ name='operator@example.com'; type='user' } } }
                'account get-access-token' { @{ accessToken='synthetic-token'; tenant=$fixtureTenant } }
                'account list-locations' { ,@(@{ name='centralus'; displayName='Central US' }) }
                'group list' { ,@(@{ id=$fixtureGroup; name='rg-portal-test'; location='centralus' }) }
                'provider show' { @{ namespace=$Arguments[5]; registrationState='Registered' } }
                'resource list' { ,@(
                    @{ id="$fixtureGroup/providers/Microsoft.Web/sites/app-portal-test"; name='app-portal-test'; type='Microsoft.Web/sites'; kind='app,linux'; location='centralus' },
                    @{ id="/subscriptions/$fixtureSubscription/resourceGroups/shared-plan/providers/Microsoft.Web/serverfarms/asp-portal-test"; name='asp-portal-test'; type='Microsoft.Web/serverfarms'; kind='linux'; location='centralus' },
                    @{ id="/subscriptions/$fixtureSubscription/resourceGroups/shared-registry/providers/Microsoft.ContainerRegistry/registries/crportaltest001"; name='crportaltest001'; type='Microsoft.ContainerRegistry/registries'; location='eastus' },
                    @{ id="/subscriptions/$fixtureSubscription/resourceGroups/shared-identity/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-portal"; name='id-portal'; type='Microsoft.ManagedIdentity/userAssignedIdentities'; location='centralus' }) }
                default { throw 'Unexpected Azure CLI command in preview' }
            }
            ConvertTo-Json -InputObject $data -Depth 20 -Compress
        }
        Mock Invoke-RestMethod {
            $fixtureGroup='/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/rg-portal-test'
            $fixtureApp="$fixtureGroup/providers/Microsoft.Web/sites/app-portal-test"
            $fixturePlan='/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/shared-plan/providers/Microsoft.Web/serverfarms/asp-portal-test'
            $fixtureRegistry='/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/shared-registry/providers/Microsoft.ContainerRegistry/registries/crportaltest001'
            $fixtureIdentity='/subscriptions/11111111-1111-4111-8111-111111111111/resourceGroups/shared-identity/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-portal'
            $fixtureClient='55555555-5555-4555-8555-555555555555'
            $fixturePrincipal='66666666-6666-4666-8666-666666666666'
            if ($Uri -like '*/config/appsettings/list?*' -and $Method -eq 'POST') { return @{ properties=@{ GALLERY_ADMIN_OIDS='33333333-3333-4333-8333-333333333333'; AZURE_CLIENT_ID=$fixtureClient; GALLERY_MANAGED_IDENTITY='user'; PRIVATE_SECRET='synthetic-only' } } }
            if ($Method -ne 'GET') { throw 'Unexpected remote write' }
            if (([string]$Uri).StartsWith('https://graph.microsoft.com/v1.0/applications?')) {
                return @{ value=@(@{ id='77777777-7777-4777-8777-777777777777'; appId='22222222-2222-4222-8222-222222222222'; signInAudience='AzureADMyOrg'
                    web=@{ redirectUris=@('https://portal.azurewebsites.net/.auth/login/aad/callback'); implicitGrantSettings=@{ enableIdTokenIssuance=$false } }
                    passwordCredentials=@(@{ endDateTime=[DateTimeOffset]::UtcNow.AddDays(30).ToString('o') }) }) }
            }
            if (([string]$Uri).StartsWith('https://graph.microsoft.com/v1.0/servicePrincipals?')) {
                return @{ value=@(@{ id='88888888-8888-4888-8888-888888888888'; appId='22222222-2222-4222-8222-222222222222'; accountEnabled=$true; appRoleAssignmentRequired=$false }) }
            }
            if ($Uri -like 'https://graph.microsoft.com/v1.0/servicePrincipals/*/appRoleAssignedTo?*') { return @{ value=@() } }
            if ($Uri -like '*/locations?*') { return @{ value=@(@{ name='centralus'; displayName='Central US' }) } }
            if ($Uri -like '*/config/authsettingsV2?*') { return @{ properties=@{ identityProviders=@{ azureActiveDirectory=@{ enabled=$true; registration=@{ clientId='22222222-2222-4222-8222-222222222222'; clientSecretSettingName='PRIVATE_SECRET'; openIdIssuer='https://login.microsoftonline.com/22222222-2222-4222-8222-222222222222/v2.0' }; validation=@{ allowedAudiences=@() } } } } } }
            if ($Uri -like '*/config/web?*') { return @{ properties=@{ linuxFxVersion='PYTHON|3.12' } } }
            if ($Uri -like "https://management.azure.com$fixtureApp`?*") { return @{ id=$fixtureApp; kind='app,linux'; location='Central US'; identity=@{ type='UserAssigned'; userAssignedIdentities=@{ $fixtureIdentity=@{ clientId=$fixtureClient; principalId=$fixturePrincipal } } }; properties=@{ serverFarmId=$fixturePlan; httpsOnly=$true; defaultHostName='portal.azurewebsites.net'; hostNames=@('portal.azurewebsites.net') } } }
            if ($Uri -like "https://management.azure.com$fixturePlan/sites`?*") { return @{ value=@(@{ id=$fixtureApp }) } }
            if ($Uri -like "https://management.azure.com$fixturePlan`?*") { return @{ id=$fixturePlan; location='Central US'; sku=@{ name='F1' }; properties=@{ reserved=$true; numberOfWorkers=1 } } }
            if ($Uri -like "https://management.azure.com$fixtureRegistry`?*") { return @{ id=$fixtureRegistry; location='eastus'; properties=@{ adminUserEnabled=$false } } }
            if ($Uri -like "https://management.azure.com$fixtureIdentity`?*") { return @{ id=$fixtureIdentity; location='centralus'; properties=@{ clientId=$fixtureClient; principalId=$fixturePrincipal } } }
            if ($Uri -like "https://management.azure.com$fixtureGroup`?*") { return @{ id=$fixtureGroup; location='centralus' } }
            throw 'Unexpected ARM/Graph destination'
        }
        $previousExitCode = $global:LASTEXITCODE
        try {
            $output = & "$PSScriptRoot/../Deploy-Portal.ps1" -Mode Prepare -ResourceGroupName 'rg-proposed-new' -ConfigPath (Join-Path $TestDrive 'entry-prepare.json') -WhatIf
            @($output | Where-Object { $_.Status -eq 'PreviewOnly' }) | Should -HaveCount 1
            ($output | Where-Object { $_.Status -eq 'PreviewOnly' }).AppResourceId | Should -Match '/resourceGroups/rg-portal-test/'
            $output.ReadyForApply | Should -BeTrue
            $output.HostingReadiness | Should -Be $(if ($upgradeChoice -eq 'Upgrade') { 'ChangePlanned' } else { 'Warning' })
            $output.EntraReadiness | Should -Be 'RequiresRepair'
            $output.EntraRepairPlanned | Should -BeTrue
            $output.RuntimeVerified | Should -BeFalse
            ($output | ConvertTo-Json -Depth 20) | Should -Not -Match 'synthetic-only|synthetic-token'
            $output.ConfigurationSaved | Should -BeTrue
            $saved = Import-PortalPrepareConfiguration $output.ConfigurationPath
            $saved.Selections.PlanResourceId | Should -Match '/resourceGroups/shared-plan/'
            $saved.Selections.RegistryResourceId | Should -Match '/resourceGroups/shared-registry/'
            $saved.Selections.ManagedIdentityResourceId | Should -Match '/resourceGroups/shared-identity/'
            ($saved | ConvertTo-Json -Depth 6) | Should -Not -Match 'synthetic-only|synthetic-token|RepairEntra|Confirm'
            $reused = & "$PSScriptRoot/../Deploy-Portal.ps1" -Mode Prepare -ConfigPath $output.ConfigurationPath -WhatIf
            $reused.ConfigurationSaved | Should -BeTrue
            $reused.AppResourceId | Should -Be $output.AppResourceId
            $reused.HostingReadiness | Should -Be $output.HostingReadiness
            Should -Invoke Read-Host -Times 2 -Exactly -ParameterFilter { $Prompt.StartsWith('Plan the listed Entra repairs?') }
            Should -Invoke az -Times 1 -Exactly -ParameterFilter { $Arguments[0] -eq 'account' -and $Arguments[1] -eq 'list' }
            Should -Invoke az -Times 0 -Exactly -ParameterFilter { $Arguments -contains 'create' -or $Arguments -contains 'register' -or $Arguments -contains 'set' -or $Arguments -contains 'start' }
            Should -Invoke Invoke-RestMethod -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH','DELETE') -or ($Method -eq 'POST' -and $Uri -notlike '*/appsettings/list?*') }
            Should -Invoke Read-Host -Times 0 -Exactly -ParameterFilter { $AsSecureString }
            Should -Invoke Invoke-RestMethod -Times 0 -Exactly -ParameterFilter { $Uri -like '*/resourceGroups/rg-proposed-new/*' }
        } finally { $global:LASTEXITCODE = $previousExitCode }
    }
}

Describe 'Entra readiness before deployment' {
    BeforeEach {
        $readinessPlan=@{ TenantId=$tenant; AdministratorIds=$admin }
        $readinessSnapshot=@{ Site=@{ properties=@{ defaultHostName='portal.azurewebsites.net'; hostNames=@('portal.azurewebsites.net') } }; Settings=@{ properties=@{} }; Authentication=@{ properties=@{} } }
        $readinessSnapshot.Settings.properties.PORTAL_SECRET = 'synthetic-private-credential'
        $readinessSnapshot.Authentication.properties.identityProviders = @{ azureActiveDirectory=@{
            enabled=$true; registration=@{ clientId=$subscription; openIdIssuer="https://login.microsoftonline.com/$tenant/v2.0"; clientSecretSettingName='PORTAL_SECRET' }
            validation=@{ allowedAudiences=@($subscription) } } }
        $script:registration=@{ id=$tenant; appId=$subscription; signInAudience='AzureADMyOrg'; web=@{ redirectUris=@('https://portal.azurewebsites.net/.auth/login/aad/callback'); implicitGrantSettings=@{ enableIdTokenIssuance=$true } } }
        $script:registration.passwordCredentials=@(@{ endDateTime=[DateTimeOffset]::UtcNow.AddDays(30).ToString('o') })
        $script:enterprise=@{ id=$admin; appId=$subscription; accountEnabled=$true; appRoleAssignmentRequired=$true }
        $script:assignments=@(@{ principalId=$admin })
        Mock Invoke-PortalFoundationGraph {
            if ($Path.StartsWith('/applications?')) { return @{ value=@($script:registration) } }
            if ($Path.StartsWith('/servicePrincipals?')) { return @{ value=@($script:enterprise) } }
            if ($Path -like '*/appRoleAssignedTo?*') { return @{ value=$script:assignments } }
            throw 'Unexpected directory call'
        }
    }

    It 'Finds callback, ID-token and assignment issues during read-only preview' {
        $script:registration.web.redirectUris=@('https://existing.example/callback')
        $script:registration.web.implicitGrantSettings.enableIdTokenIssuance=$false
        $script:enterprise.appRoleAssignmentRequired=$false
        $script:assignments=@()
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'RequiresRepair'
        @($result.Checks | Where-Object { $_.Status -eq 'Repairable' }) | Should -HaveCount 4
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -in @('POST','PATCH') }
    }

    It 'Reports a fully configured reused registration and verifies operator read access' {
        Mock Write-Host {}
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'Ready'
        $result.TenantId | Should -Be $readinessPlan.TenantId
        @($result.Checks | Where-Object { $_.Status -ne 'Ready' }) | Should -HaveCount 0
        ($result.Checks | Where-Object Check -eq 'Operator directory read access').Status | Should -Be 'Ready'
        Write-PortalEntraReadiness $result
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Entra registration correctly configured*' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -eq "[ACTION] Administrator consent page: https://login.microsoftonline.com/$($readinessPlan.TenantId)/v2.0/adminconsent?client_id=$subscription&scope=openid%20profile%20email&redirect_uri=https%3A%2F%2Fportal.azurewebsites.net%2F.auth%2Flogin%2Faad%2Fcallback" }
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -match 'synthetic-private-credential' }
        Should -Invoke Invoke-PortalFoundationGraph -Times 3 -Exactly
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -in @('POST','PATCH') }
    }

    It 'Reports all independently verifiable configuration problems together' {
        $script:registration.signInAudience='AzureADMultipleOrgs'
        $script:registration.web.implicitGrantSettings.enableIdTokenIssuance=$false
        $script:enterprise.accountEnabled=$false
        $script:registration.passwordCredentials=@(@{ endDateTime=[DateTimeOffset]::UtcNow.AddDays(-1).ToString('o') })
        $readinessSnapshot.Settings.properties.Clear()
        $provider=$readinessSnapshot.Authentication.properties.identityProviders.azureActiveDirectory
        $provider.enabled=$false
        $provider.registration.openIdIssuer='https://login.microsoftonline.com/common/v2.0'
        $provider.validation.allowedAudiences=@($subscription,$admin)
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'Blocked'
        $blocked=@($result.Checks | Where-Object Status -eq 'Blocked')
        $blocked | Should -HaveCount 7
        foreach ($checkName in @('Registration tenant','Enterprise application enabled','Host Entra provider','Host token issuer','Host token audiences','Protected provider credential setting','Unexpired registration credential metadata')) {
            $blocked.Check | Should -Contain $checkName
        }
        ($result.Checks | Where-Object Check -eq 'Entra ID tokens').Status | Should -Be 'Repairable'
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -in @('POST','PATCH') }
    }

    It 'Does not certify an unconnected host as correctly configured' {
        Mock Write-Host {}
        $readinessSnapshot.Authentication.properties.Clear()
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        ($result.Checks | Where-Object Check -eq 'Host Entra provider').Status | Should -Be 'Pending'
        Write-PortalEntraReadiness $result
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -like '*Entra registration correctly configured*' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '[[]PLAN[]] Host Entra provider*Script step after approval*script will connect*' }
    }

    It 'Identifies a missing registration rather than suggesting a permission change' {
        $script:registration.appId=$admin
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'Blocked'
        $result.Checks[-1].Detail | Should -Match 'client ID did not resolve'
    }

    It 'Identifies a missing enterprise application explicitly' {
        $script:enterprise.appId=$tenant
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'Blocked'
        $result.Checks[-1].Detail | Should -Match 'enterprise application is missing'
    }

    It 'Names denied operator permissions during <Stage>' -ForEach @(
        @{ Pattern='/applications?*'; Stage='Directory registration read' },
        @{ Pattern='/servicePrincipals?*'; Stage='Enabled enterprise application' },
        @{ Pattern='*/appRoleAssignedTo?*'; Stage='Enterprise application assignments' }
    ) {
        $deniedPattern=$Pattern
        Mock Invoke-PortalFoundationGraph {
            $failure=[InvalidOperationException]::new('synthetic-private-error')
            $failure.Data['HttpStatus']=403
            throw $failure
        } -ParameterFilter { $Path -like $deniedPattern }
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'Blocked'
        $result.Checks[-1].Check | Should -Be $Stage
        $result.Checks[-1].Detail | Should -Match 'Application.Read.All'
        $result.Checks[-1].Detail | Should -Match 'operator/CLI, not the portal registration'
        ($result.Checks | ConvertTo-Json) | Should -Not -Match 'synthetic-private'
    }

    It 'Blocks unreadable or wrong-tenant registration metadata without leaking errors' {
        Mock Invoke-PortalFoundationGraph { throw 'synthetic-sensitive-error' }
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'Blocked'
        ($result | ConvertTo-Json -Depth 10) | Should -Not -Match 'synthetic-sensitive-error'
    }

    It 'Reports a sanitized directory HTTP status rather than claiming readiness' {
        Mock Invoke-PortalFoundationGraph { $failure=[InvalidOperationException]::new('synthetic-secret-remote-body'); $failure.Data['HttpStatus']=401; throw $failure }
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        $result.Status | Should -Be 'Blocked'
        $result.Checks[-1].Detail | Should -Match 'HTTP 401'
        ($result | ConvertTo-Json -Depth 10) | Should -Not -Match 'synthetic-secret'
    }

    It 'Does not repair any directory settings during WhatIf' {
        $script:enterprise.appRoleAssignmentRequired=$false
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        Repair-PortalEntraReadiness $readinessPlan $readinessSnapshot $result -WhatIf | Should -BeFalse
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -in @('POST','PATCH') }
    }

    It 'Preserves callbacks and unrelated grant settings during confirmed repair' {
        $script:registration.web.redirectUris=@('https://existing.example/callback')
        $script:registration.web.implicitGrantSettings=@{ enableIdTokenIssuance=$false; enableAccessTokenIssuance=$false }
        $script:enterprise.appRoleAssignmentRequired=$false
        $result=Get-PortalEntraReadiness $readinessPlan $readinessSnapshot $subscription Existing
        Mock Invoke-PortalFoundationGraph {
            if ($Path.StartsWith('/applications?')) { return @{ value=@($script:registration) } }
            if ($Path.StartsWith('/servicePrincipals?')) { return @{ value=@($script:enterprise) } }
            if ($Path -like '*/appRoleAssignedTo?*') { return @{ value=$script:assignments } }
            if ($Method -eq 'PATCH' -and $Path -like '/applications/*') { $script:registration.web=$Body.web; return @{} }
            if ($Method -eq 'PATCH' -and $Path -like '/servicePrincipals/*') { $script:enterprise.appRoleAssignmentRequired=$true; return @{} }
            throw 'Unexpected directory call'
        }
        Repair-PortalEntraReadiness $readinessPlan $readinessSnapshot $result -Confirm:$false | Should -BeTrue
        $script:registration.web.redirectUris | Should -Contain 'https://existing.example/callback'
        $script:registration.web.redirectUris | Should -Contain 'https://portal.azurewebsites.net/.auth/login/aad/callback'
        $script:registration.web.implicitGrantSettings.enableAccessTokenIssuance | Should -BeFalse
    }
}

Describe 'Entra reuse result output' {
    BeforeEach {
        Mock Write-Host {}
        Mock Write-Warning {}
    }

    It 'Reports correctly configured only when every check passes' {
        Write-PortalEntraReadiness @{ Status='Ready'; Checks=@(@{ Check='Employee access'; Status='Ready'; Detail='Assigned' }) }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Entra registration correctly configured*' -and $ForegroundColor -eq 'Green' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*still require a browser sign-in test*' }
        Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter { $Message -like '*Admin consent is NOT VERIFIED*Assigning users does not grant consent*' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Keep Assignment required enabled*' }
    }

    It 'Includes the selected tenant in the consent action without directory or browser calls' {
        Mock Write-PortalConsentAction {}
        Mock Invoke-PortalFoundationGraph { throw 'Unexpected directory call' }
        Mock Start-Process { throw 'Unexpected browser launch' }
        Write-PortalEntraReadiness @{ Status='Ready'; TenantId=$tenant; ClientId='44444444-4444-4444-8444-444444444444'; Origin='https://portal.azurewebsites.net'; Checks=@(@{ Check='Employee access'; Status='Ready' }) }
        Should -Invoke Write-PortalConsentAction -Times 1 -Exactly -ParameterFilter { $TenantId -eq $tenant -and $ClientId -eq '44444444-4444-4444-8444-444444444444' -and $Origin -eq 'https://portal.azurewebsites.net' }
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly
        Should -Invoke Start-Process -Times 0 -Exactly
    }

    It 'Does not certify a pending, blocked or incomplete registration: <_>' -ForEach @('Pending','Blocked','Repairable','Empty') {
        $checks = if ($_ -eq 'Empty') { @() } else { @(@{ Check='Entra configuration'; Status=$_; Detail='Needs verification' }) }
        Write-PortalEntraReadiness @{ Status='Ready'; Checks=$checks }
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -like '*Entra registration correctly configured*' }
        if ($_ -eq 'Blocked') {
            Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '[[]BLOCKED[]] Entra has issues requiring attention*' }
        } elseif ($_ -ne 'Empty') {
            Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Entra script steps remaining*' }
        } else {
            Should -Invoke Write-Warning -Times 1 -Exactly -ParameterFilter { $Message -like '*no complete set of passing checks*' }
        }
    }

    It 'Prints only actionable findings without exposing private registration data' {
        Write-PortalEntraReadiness @{ Status='RequiresRepair'; Application=@{ secret='synthetic-private' }; Checks=@(
            @{ Check='Callback'; Status='Ready'; Detail='Already correct' },
            @{ Check='Entra ID tokens'; Status='Repairable'; Detail='Enable ID tokens' }) }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*Entra ID tokens*Script repair after approval*: Enable ID tokens*' -and $ForegroundColor -eq 'Cyan' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*existing directory application-write authority*' -and $ForegroundColor -eq 'Gray' }
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -match 'Already correct|synthetic-private|correctly configured' }
    }

    It 'Labels callback repair and provider connection as script work, not manual fixes' {
        Mock Invoke-PortalFoundationGraph { throw 'Unexpected directory operation' }
        Write-PortalEntraReadiness @{Status='RequiresRepair';Checks=@(
            @{Check='Entra callback';Status='Repairable';Detail='After approval, the script will add the callback.'},
            @{Check='Host Entra provider';Status='Pending';Detail='After confirmation, the script will connect the provider.'})}
        Should -Invoke Write-Host -Times 2 -Exactly -ParameterFilter { $Object -like '[[]PLAN[]]*' }
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -match '^\[ACTION\]|AppRoleAssignment.ReadWrite.All|validation incomplete' }
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*WhatIf only previews*' }
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly
    }

    It 'Mentions assignment permissions only for assignment repairs' {
        Write-PortalEntraReadiness @{Status='RequiresRepair';Checks=@(@{Check='Initial administrator app access';Status='Repairable';Detail='Script will assign designated administrators after approval.'})}
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '*AppRoleAssignment.ReadWrite.All*' -and $ForegroundColor -eq 'Gray' }
    }

    It 'Does not promise script repairs for deferred identity setup' {
        $readiness=Get-PortalEntraReadiness @{} @{} '' Later
        Write-PortalEntraReadiness $readiness
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '[[]ACTION[]]*Deferred by choice*' }
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -like '[[]PLAN[]]*' }
    }

    It 'Describes new registration creation as a script step after approval' {
        Write-PortalEntraReadiness (Get-PortalEntraReadiness @{} @{} '' Create)
        Should -Invoke Write-Host -Times 1 -Exactly -ParameterFilter { $Object -like '[[]PLAN[]]*script will create and configure the registration*' }
        Should -Invoke Write-Host -Times 0 -Exactly -ParameterFilter { $Object -like '[[]ACTION[]]*' }
    }
}

Describe 'Background hosting remediation' {
    BeforeEach {
        $plan = @{ SubscriptionId=$subscription; PlanId="/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Web/serverfarms/plan"
            AppResourceId="/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Web/sites/portal"
            HostingReadiness=@{ UpgradeRequired=$true; CurrentSku='F1'; TargetSku='B1' } }
        $snapshot = @{ PlanSites=@{ value=@(@{ id=$plan.AppResourceId }) } }
    }

    It 'Does not upgrade a shared plan in WhatIf' {
        Mock Invoke-PortalPrerequisiteResource { throw 'Unexpected mutation' }
        Set-PortalPlanUpgrade $plan $snapshot -WhatIf | Should -BeFalse
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly
    }

    It 'Upgrades only the SKU and verifies readback with explicit confirmation disabled by the operator' {
        $script:paidSku='F1'
        Mock Invoke-PortalPrerequisiteResource {
            if ($ResourceId -like '*/sites') { return $snapshot.PlanSites }
            if ($Method -eq 'PATCH') { $script:paidSku=$Body.sku.name }
            return @{ sku=@{ name=$script:paidSku }; properties=@{ numberOfWorkers=1 } }
        }
        Set-PortalPlanUpgrade $plan $snapshot -Confirm:$false | Should -BeTrue
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { $Method -eq 'PATCH' -and $Body.Count -eq 1 -and $Body.sku.name -eq 'B1' }
    }

    It 'Refuses plan changes when its shared apps changed' {
        Mock Invoke-PortalPrerequisiteResource {
            if ($ResourceId -like '*/sites') { return @{ value=@(@{ id='another-app' }) } }
            return @{ sku=@{ name='F1' }; properties=@{ numberOfWorkers=1 } }
        }
        { Set-PortalPlanUpgrade $plan $snapshot -Confirm:$false } | Should -Throw '*changed after preview*'
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -eq 'PATCH' }
    }

    It 'Enables Always On without changing other web settings' {
        Mock Invoke-PortalPrerequisiteResource { return @{ sku=@{ name='B1' } } }
        $script:webProperties=@{ alwaysOn=$false; linuxFxVersion='DOCKER|existing'; appCommandLine='existing-startup' }
        Mock Invoke-PortalArm {
            if ($Method -eq 'PUT') { $script:webProperties=$Body.properties }
            return @{ properties=@{} + $script:webProperties }
        }
        Enable-PortalAlwaysOn $plan
        $script:webProperties.alwaysOn | Should -BeTrue
        $script:webProperties.linuxFxVersion | Should -Be 'DOCKER|existing'
        $script:webProperties.appCommandLine | Should -Be 'existing-startup'
    }

    It 'Skips Always On configuration on <_> without writes' -ForEach @('F1','D1') {
        $hostingSku = $_
        Mock Invoke-PortalPrerequisiteResource { return @{ sku=@{ name=$hostingSku } } }
        Mock Invoke-PortalArm { throw 'Unexpected Always On request' }
        Enable-PortalAlwaysOn $plan
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 0 -Exactly -ParameterFilter { $Method -in @('PUT','PATCH') }
    }
}

Describe 'Tenant registration discovery' {
    It 'Includes differently named registrations from subsequent pages' {
        Mock Invoke-PortalFoundationGraph {
            if ($Path -like '*skiptoken*') { return @{ value=@(@{ appId=$admin; displayName='Employee Access'; signInAudience='AzureADMyOrg' }) } }
            return @{ value=@(@{ appId=$subscription; displayName='Organization Login'; signInAudience='AzureADMyOrg' }); '@odata.nextLink'='https://graph.microsoft.com/v1.0/applications?$skiptoken=next' }
        }
        $registrations = @(Get-PortalTenantRegistrations $tenant)
        $registrations | Should -HaveCount 2
        $registrations.displayName | Should -Contain 'Employee Access'
        Should -Invoke Invoke-PortalFoundationGraph -Times 2 -Exactly
    }

    It 'Rejects directory pagination outside the application collection' {
        Mock Invoke-PortalFoundationGraph { return @{ value=@(); '@odata.nextLink'='https://untrusted.example/applications?$skiptoken=next' } }
        { Get-PortalTenantRegistrations $tenant } | Should -Throw '*pagination destination*'
        Should -Invoke Invoke-PortalFoundationGraph -Times 1 -Exactly
    }
}

Describe 'Managed identity reuse' {
    BeforeEach {
        $script:identityId = "/subscriptions/$subscription/resourceGroups/shared/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-shared"
        $script:identityClient = $tenant
        $script:identityPrincipal = $admin
        $identityInputs = @{ SubscriptionId=$subscription; ResourceGroupName='hosting'; AppName='portal'; Location='centralus'; ManagedIdentityResourceId=$script:identityId }
        $identityPlan = @{ SubscriptionId=$subscription; AppResourceId="/subscriptions/$subscription/resourceGroups/hosting/providers/Microsoft.Web/sites/portal"; ManagedIdentityId=$script:identityId; Inputs=$identityInputs }
    }

    It 'Offers existing identities and a new named identity or system identity' {
        $identityInputs.Remove('ManagedIdentityResourceId')
        Mock Read-Host { return $script:identityId }
        Read-PortalManagedIdentityChoice $identityInputs @(@{ id=$script:identityId; type='Microsoft.ManagedIdentity/userAssignedIdentities'; name='id-shared'; location='eastus' }) @{} @{}
        $identityInputs.ManagedIdentityResourceId | Should -Be $script:identityId
        Should -Invoke Read-Host -Times 1 -Exactly
    }

    It 'Retains an already configured attached user identity without a switch prompt' {
        $identityInputs.Remove('ManagedIdentityResourceId')
        $site = @{ identity=@{ userAssignedIdentities=@{ $script:identityId=@{ clientId=$tenant; principalId=$admin } } } }
        Mock Read-Host { throw 'Unexpected identity replacement prompt' }
        Read-PortalManagedIdentityChoice $identityInputs @() $site @{ AZURE_CLIENT_ID=$tenant }
        $identityInputs.ManagedIdentityResourceId | Should -Be $script:identityId
    }

    It 'Attaches a reused identity without removing the system identity or other attachments' {
        $otherIdentity = "/subscriptions/$subscription/resourceGroups/shared/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-other"
        $site = @{ identity=@{ type='SystemAssigned, UserAssigned'; principalId=$subscription; userAssignedIdentities=@{ $otherIdentity=@{ clientId=$subscription } } } }
        Mock New-PortalPrerequisiteResource { return @{ properties=@{ clientId=$script:identityClient; principalId=$script:identityPrincipal } } }
        Mock Invoke-PortalPrerequisiteResource { return @{ identity=@{ userAssignedIdentities=@{ $script:identityId=@{ clientId=$script:identityClient } } } } }
        $result = Set-PortalHostIdentity $identityPlan $site @{}
        $result.Mode | Should -Be 'user'
        $result.PrincipalId | Should -Be $admin
        Should -Invoke New-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { $RequireExisting }
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { $Method -eq 'PATCH' -and $Body.identity.type -eq 'SystemAssigned, UserAssigned' -and $Body.identity.userAssignedIdentities.Count -eq 2 }
    }

    It 'Rejects an identity in another subscription before any resource request' {
        $identityInputs.ManagedIdentityResourceId = $script:identityId.Replace($subscription,$tenant)
        { Get-PortalManagedIdentityId $identityInputs } | Should -Throw '*selected subscription*'
    }

    It 'Creates a named identity and attaches it to a new app with no user attachments' {
        $identityInputs.Remove('ManagedIdentityResourceId')
        $identityInputs.NewManagedIdentityName='id-new-portal'
        $identityPlan.ManagedIdentityId = Get-PortalManagedIdentityId $identityInputs
        $script:newIdentityId = $identityPlan.ManagedIdentityId
        $site = @{ identity=@{ type='SystemAssigned'; principalId=$subscription } }
        Mock New-PortalPrerequisiteResource { return @{ properties=@{ clientId=$script:identityClient; principalId=$script:identityPrincipal } } }
        Mock Invoke-PortalPrerequisiteResource { return @{ identity=@{ userAssignedIdentities=@{ $script:newIdentityId=@{ clientId=$script:identityClient } } } } }
        (Set-PortalHostIdentity $identityPlan $site @{}).ClientId | Should -Be $tenant
        Should -Invoke New-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { -not $RequireExisting -and $Body.location -eq 'centralus' -and $ResourceId -eq $script:newIdentityId }
        Should -Invoke Invoke-PortalPrerequisiteResource -Times 1 -Exactly -ParameterFilter { $Method -eq 'PATCH' -and $Body.identity.userAssignedIdentities.Count -eq 1 }
    }

    It 'Plans a missing named identity but rejects a missing selected existing identity' {
        $hosting = @{ SubscriptionId=$subscription; ResourceGroupName='hosting'; AppName='portal'; PlanName='plan'; RegistryName='registry001'; Location='centralus'; PlanSku='B1'; AdminObjectIds=@($admin); NewManagedIdentityName='id-portal' }
        $snapshot = @{ Account=@{ id=$subscription; tenantId=$tenant; state='Enabled'; environmentName='AzureCloud' } }
        $plan = Get-PortalPrerequisitePlan $hosting $snapshot
        $plan.ManagedIdentityId | Should -Match '/hosting/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-portal$'
        $plan.Steps[-1].Action | Should -Match 'Create and attach'
        $hosting.Remove('NewManagedIdentityName')
        $hosting.ManagedIdentityResourceId=$plan.ManagedIdentityId
        { Get-PortalPrerequisitePlan $hosting $snapshot } | Should -Throw '*unavailable*'
    }

    It 'Offers creation of a named identity with an editable default' {
        $identityInputs.Remove('ManagedIdentityResourceId')
        Mock Read-Host { if ($Prompt.StartsWith('Host managed identity')) { return 'New' }; return '' }
        Read-PortalManagedIdentityChoice $identityInputs @() @{} @{}
        $identityInputs.NewManagedIdentityName | Should -Be 'id-portal'
    }
}

Describe 'Organization Entra creation' {
    BeforeEach {
        $plan = @{ TenantId = $tenant; SubscriptionId = $subscription; AdministratorIds = $admin; AppResourceId = "/subscriptions/$subscription/resourceGroups/test/providers/Microsoft.Web/sites/portal" }
        Mock Test-PortalEntraRegistration {}
        Mock Connect-PortalPrerequisiteEntra {}
        Mock Invoke-PortalArm { return @{ properties = @{} } }
    }

    It 'Does not create a registration or credential during WhatIf' {
        Mock Invoke-PortalFoundationGraph { throw 'Unexpected Graph write' }
        (New-PortalCustomerRegistration $plan 'https://portal.azurewebsites.net' 'Company Portal' -WhatIf).Status | Should -Be 'PreviewOnly'
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly
        Should -Invoke Invoke-PortalArm -Times 0 -Exactly
    }

    It 'Creates a single-tenant app and stores the credential without returning it' {
        Mock Invoke-PortalFoundationGraph {
            if ($Path -eq '/applications') { return @{ id = $admin; appId = $subscription } }
            if ($Path -eq '/servicePrincipals') { return @{ id = $tenant; appId = $subscription; appRoleAssignmentRequired = $true } }
            if ($Path -like '/users/*') { return @{ id = $admin; userType = 'Member'; accountEnabled = $true } }
            if ($Path -like '*/addPassword') { return @{ secretText = 'synthetic-credential-only' } }
            return @{ value = @() }
        }
        $result = New-PortalCustomerRegistration $plan 'https://portal.azurewebsites.net' 'Company Portal' -Confirm:$false
        $result.ClientId | Should -Be $subscription
        ($result | ConvertTo-Json) | Should -Not -Match 'synthetic-credential'
        Should -Invoke Invoke-PortalFoundationGraph -Times 1 -Exactly -ParameterFilter { $Path -eq '/applications' -and $Body.signInAudience -eq 'AzureADMyOrg' -and
            $Body.web.implicitGrantSettings.enableIdTokenIssuance -eq $true -and $Body.web.redirectUris[0] -eq 'https://portal.azurewebsites.net/.auth/login/aad/callback' }
        Should -Invoke Invoke-PortalArm -Times 1 -Exactly -ParameterFilter { $Method -eq 'PUT' -and $Body.properties.PORTAL_ENTRA_CLIENT_SECRET -eq 'synthetic-credential-only' }
        Should -Invoke Connect-PortalPrerequisiteEntra -Times 1 -Exactly
        Should -Invoke Invoke-PortalFoundationGraph -Times 1 -Exactly -ParameterFilter { $Path -eq '/servicePrincipals' -and $Body.appRoleAssignmentRequired -eq $true }
        Should -Invoke Invoke-PortalFoundationGraph -Times 1 -Exactly -ParameterFilter { $Path -like '*/appRoleAssignedTo' -and $Method -eq 'POST' -and $Body.principalId -eq $admin }
    }

    It 'Never adopts a same-name registration without the deployment marker' {
        Mock Invoke-PortalFoundationGraph { return @{ value = @(@{ id = $admin; appId = $subscription; tags = @() }) } }
        { New-PortalCustomerRegistration $plan 'https://portal.azurewebsites.net' 'Company Portal' -Confirm:$false } | Should -Throw '*same-name*'
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -eq 'POST' }
    }

    It 'Retains a prior successful registration without issuing another credential' {
        Mock Invoke-PortalArm {
            if ($Path -like '*/appsettings*') { return @{ properties = @{ PORTAL_ENTRA_CLIENT_ID = $admin } } }
            return @{ properties = @{ identityProviders = @{ azureActiveDirectory = @{ registration = @{ clientId = $admin } } } } }
        }
        Mock Invoke-PortalFoundationGraph { throw 'Unexpected Graph mutation' }
        (New-PortalCustomerRegistration $plan 'https://portal.azurewebsites.net' 'Company Portal' -Confirm:$false).Status | Should -Be 'RegistrationRetained'
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly
    }

    It 'Does not connect Entra after protected credential storage fails' {
        Mock Invoke-PortalFoundationGraph {
            if ($Path -eq '/applications') { return @{ id = $admin; appId = $subscription } }
            if ($Path -eq '/servicePrincipals') { return @{ id = $tenant; appId = $subscription; appRoleAssignmentRequired = $true } }
            if ($Path -like '/users/*') { return @{ id = $admin; userType = 'Member'; accountEnabled = $true } }
            if ($Path -like '*/addPassword') { return @{ secretText = 'synthetic-credential-only' } }
            return @{ value = @() }
        }
        Mock Invoke-PortalArm {
            if ($Method -eq 'PUT') { throw 'Protected storage unavailable' }
            return @{ properties = @{} }
        }
        { New-PortalCustomerRegistration $plan 'https://portal.azurewebsites.net' 'Company Portal' -Confirm:$false } | Should -Throw '*storage unavailable*'
        Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
    }

    It 'Does not assign initial application access to guests or disabled users' -ForEach @('Guest','Disabled') {
        $userType = if ($_ -eq 'Guest') { 'Guest' } else { 'Member' }
        $enabled = $_ -ne 'Disabled'
        Mock Invoke-PortalFoundationGraph {
            if ($Path -eq '/applications') { return @{ id=$admin; appId=$subscription } }
            if ($Path -eq '/servicePrincipals') { return @{ id=$tenant; appRoleAssignmentRequired=$true } }
            if ($Path -like '/users/*') { return @{ id=$admin; userType=$userType; accountEnabled=$enabled } }
            return @{ value=@() }
        }
        { New-PortalCustomerRegistration $plan 'https://portal.azurewebsites.net' 'Company Portal' -Confirm:$false } | Should -Throw '*enabled organization member users*'
        Should -Invoke Invoke-PortalFoundationGraph -Times 0 -Exactly -ParameterFilter { $Method -eq 'POST' -and ($Path -like '*/appRoleAssignedTo' -or $Path -like '*/addPassword') }
        Should -Invoke Connect-PortalPrerequisiteEntra -Times 0 -Exactly
    }
}
