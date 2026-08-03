<#
.SYNOPSIS
    Build site/capacity.json - Azure capacity/quota by constrained service and
    region for the SME&C Techlabs gallery.

.DESCRIPTION
    Decouples capacity checking from the deploy path. This runs centrally (daily
    in CI, or on demand) and writes a static capacity.json the gallery loads to
    colour-code the deploy region picker and render the Capacity page:

      green  = capacity available
      yellow = limited / unknown - may hit constraints
      red    = no capacity (a required service can't be placed here)

    Constrained services probed per region:
      * Azure OpenAI model capacities (TPM) for the models our use cases deploy
        - REST: Microsoft.CognitiveServices/modelCapacities
      * Azure App Service plan compute - ARM preflight validate of a minimal B1
        serverFarm (the only reliable signal for serverFarms quota)
      * Virtual Machine cores - REST: Microsoft.Compute/locations/usages (Total Regional vCPUs)

    Each probe is best-effort; failures degrade to status 'unknown' (yellow) so a
    transient error never blocks the gallery.

    Auth: uses the Azure CLI context (az login locally, or azure/login OIDC in
    CI). Read-only except for short-lived temp resource groups used purely for
    App Service preflight validation, which are deleted immediately.

.EXAMPLE
    ./tools/Build-Capacity.ps1

.EXAMPLE
    ./tools/Build-Capacity.ps1 -Regions eastus,centralus,westus2 -SkipAppService
#>
[CmdletBinding()]
param(
    [string]$OutFile,

    # Subscription to measure. Defaults to the az CLI's current subscription.
    [string]$SubscriptionId,

    # Regions to scan. Americas public regions (GA; canary/partner excluded),
    # matching the model/SKU registry (tools/Build-Registry.ps1).
    [string[]]$Regions = @(
        'eastus', 'eastus2', 'centralus', 'northcentralus', 'southcentralus',
        'westus', 'westus2', 'westus3', 'westcentralus',
        'canadacentral', 'canadaeast',
        'brazilsouth', 'brazilsoutheast',
        'mexicocentral'
    ),

    # OpenAI models the catalog deploys (name:version:sku). Empty means derive the
    # set from the catalog's model deployments (see Get-CatalogOpenAiModels). The
    # SKU matters: a region can have one SKU (e.g. GlobalStandard) but not another
    # (regional Standard), which fails preflight.
    [string[]]$OpenAiModels = @(),

    # Skip individual probes (faster offline/partial runs).
    [switch]$SkipOpenAi,
    [switch]$SkipAppService,
    [switch]$SkipVm,
    [switch]$SkipContainerApps,
    [switch]$SkipAvs,
    [switch]$SkipFabric,
    [switch]$SkipPowerBi,
    [switch]$SkipPurview
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutFile) { $OutFile = Join-Path $RepoRoot 'site/capacity.json' }

# --- thresholds: above 'green', between 'yellow', at/below 0 'red' -----------
$VmCoresGreen = 16     # spare regional vCPUs to call it green
$VmCoresYellow = 1     # any spare → yellow, else red
$OpenAiTpmGreen = 50   # spare TPM (thousands) to call it green
$OpenAiTpmYellow = 1

function Invoke-Az {
    param([string[]]$CliArgs)
    $out = & az @CliArgs 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($out -join "`n")
}

function Get-SubscriptionId {
    if ($SubscriptionId) { return $SubscriptionId }
    $id = Invoke-Az @('account', 'show', '--query', 'id', '-o', 'tsv')
    if (-not $id) { throw 'Not logged in to Azure CLI (az login) and no -SubscriptionId given.' }
    return $id.Trim()
}

function Get-AccessToken {
    $tok = Invoke-Az @('account', 'get-access-token', '--resource', 'https://management.azure.com', '--query', 'accessToken', '-o', 'tsv')
    if (-not $tok) { throw 'Could not acquire ARM access token.' }
    return $tok.Trim()
}

# Distinct "model:version:sku" the gallery can deploy or let the user pick, from
# every use case's metadata quota requirements (kind = model-tpm) UNION every
# OpenAI model the registry offers in advanced options (model x version x sku).
# Deriving from both keeps the probe set in lock-step with what a user can
# actually select, so a chosen model/version/SKU always has a matching signal.
function Get-CatalogOpenAiModels {
    $seen = [ordered]@{}
    $add = {
        param($model, $ver, $sku)
        if (-not $model) { return }
        if (-not $ver) { $ver = '' }
        if (-not $sku) { $sku = 'Standard' }
        $spec = "${model}:${ver}:$sku"
        if (-not $seen.Contains($spec)) { $seen[$spec] = $true }
    }
    $catalogDir = Join-Path $RepoRoot 'catalog'
    Get-ChildItem -Path $catalogDir -Recurse -Filter 'metadata.json' -File | ForEach-Object {
        try { $meta = Get-Content $_.FullName -Raw | ConvertFrom-Json } catch { return }
        foreach ($req in @($meta.quota.requirements)) {
            if ("$($req.kind)" -ne 'model-tpm' -or -not $req.model) { continue }
            & $add $req.model $req.modelVersion $req.sku
        }
    }
    $regPath = Join-Path $RepoRoot 'site/registry.json'
    if (Test-Path $regPath) {
        try {
            $reg = Get-Content $regPath -Raw | ConvertFrom-Json
            foreach ($group in @('chat', 'embedding')) {
                foreach ($m in @($reg.models.$group)) {
                    if ("$($m.provider)" -ne 'OpenAI' -or -not $m.name) { continue }
                    $skus = if ($m.skus) { @($m.skus) } else { @('Standard') }
                    foreach ($s in $skus) { & $add $m.name $m.version $s }
                }
            }
        }
        catch { }
    }
    return @($seen.Keys)
}

function New-Status {
    param([string]$Status, [string]$Detail, $Value = $null)
    return [ordered]@{ status = $Status; detail = $Detail; value = $Value }
}

# --------------------------------------------------------------------------- #
# Probe: Azure OpenAI model capacities (TPM available per region)
# --------------------------------------------------------------------------- #
function Get-OpenAiCapacity {
    param([string]$Sub, [string]$Token, [string]$ModelName, [string]$ModelVersion, [string]$SkuName = 'Standard')

    $headers = @{ Authorization = "Bearer $Token" }
    $uri = "https://management.azure.com/subscriptions/$Sub/providers/Microsoft.CognitiveServices/modelCapacities" +
    "?api-version=2024-10-01&modelFormat=OpenAI&modelName=$ModelName&modelVersion=$ModelVersion"
    $byRegion = @{}
    try {
        $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
    }
    catch {
        # API call failed entirely -> caller marks every region 'unknown'.
        return [ordered]@{ ok = $false; byRegion = $byRegion }
    }
    # The API returns one entry per (region, SKU). We only care about the SKU the
    # catalog deploys: a region missing that SKU can't host the deployment, so it
    # must not be reported green just because another SKU has capacity there.
    foreach ($entry in @($resp.value)) {
        if (($entry.properties.skuName).ToString().ToLower() -ne $SkuName.ToLower()) { continue }
        $loc = ($entry.location).ToString().ToLower()
        # availableCapacity is in TPM thousands for this SKU.
        $avail = [int]($entry.properties.availableCapacity)
        if ($avail -ge $OpenAiTpmGreen) {
            $byRegion[$loc] = New-Status 'green' "$avail K TPM available" $avail
        }
        elseif ($avail -ge $OpenAiTpmYellow) {
            $byRegion[$loc] = New-Status 'yellow' "only $avail K TPM available" $avail
        }
        else {
            $byRegion[$loc] = New-Status 'red' 'no TPM capacity in this region' $avail
        }
    }
    return [ordered]@{ ok = $true; byRegion = $byRegion }
}

# --------------------------------------------------------------------------- #
# Probe: per-region model/SKU lifecycle status + deprecation date
#
# modelCapacities reports TPM but NOT lifecycle state, so a model can show
# capacity yet still be blocked for new deployments (ServiceModelDeprecating /
# ServiceModelDeprecated). Azure rejects a NEW deployment as soon as the model's
# lifecycleStatus becomes 'Deprecating' or 'Deprecated' - this happens BEFORE the
# published deprecationDate, so the date alone is not enough to gate on.
# The locations/<region>/models API exposes both the model's lifecycleStatus and
# each SKU's deprecationDate; we capture both per region so callers can veto a
# region when a model is no longer deployable there.
# --------------------------------------------------------------------------- #
function Get-RegionModelDeprecations {
    param([string]$Sub, [string]$Token, [string]$Region)

    $headers = @{ Authorization = "Bearer $Token" }
    $uri = "https://management.azure.com/subscriptions/$Sub/providers/Microsoft.CognitiveServices/locations/$Region/models?api-version=2024-10-01"
    $map = @{}  # key 'name|version|sku-lower' -> @{ date=<string|$null>; status=<lifecycleStatus> }
    try {
        $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
    }
    catch {
        return $null  # query failed -> caller treats deprecation as unknown
    }
    foreach ($entry in @($resp.value)) {
        $m = $entry.model
        if (-not $m) { continue }
        $status = if ($m.lifecycleStatus) { $m.lifecycleStatus.ToString() } else { '' }
        foreach ($s in @($m.skus)) {
            $key = "$($m.name)|$($m.version)|$(($s.name).ToString().ToLower())"
            $map[$key] = @{ date = $s.deprecationDate; status = $status }
        }
    }
    return $map
}

# --------------------------------------------------------------------------- #
# Probe: VM regional + per-family core quota vs usage
#
# Uses the ARM usages REST API directly (same bearer-token path as every other
# probe) rather than `az vm list-usage`: the CLI shell-out returned nothing in
# CI, leaving every region 'usage query failed'. Returns the usage entries array
# so callers can match name.value ('cores' or a family token) as before.
# --------------------------------------------------------------------------- #
function Get-VmUsage {
    param([string]$Sub, [string]$Token, [string]$Region)
    $headers = @{ Authorization = "Bearer $Token" }
    $uri = "https://management.azure.com/subscriptions/$Sub/providers/Microsoft.Compute/locations/$Region/usages?api-version=2024-07-01"
    try {
        $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers
    }
    catch {
        return $null
    }
    return @($resp.value)
}

# green/yellow/red from a single vm list-usage entry (limit vs currentValue).
function Get-CoreStatus {
    param($Entry, [string]$What)
    if (-not $Entry) { return New-Status 'unknown' "$What quota not reported in this region" }
    $spare = [int]$Entry.limit - [int]$Entry.currentValue
    if ($spare -ge $VmCoresGreen) { return New-Status 'green' "$spare of $($Entry.limit) $What vCPUs free" $spare }
    if ($spare -ge $VmCoresYellow) { return New-Status 'yellow' "only $spare $What vCPUs free" $spare }
    return New-Status 'red' "0 of $($Entry.limit) $What vCPUs free - request quota" $spare
}

# Map an Azure VM size to its quota family token (matches list-usage name.value),
# e.g. Standard_D4s_v5 -> standardDSv5Family, Standard_F4s_v2 -> standardFSv2Family.
function Convert-VmSizeToFamily {
    param([string]$Size)
    if ($Size -match '^Standard_([A-Za-z]+)\d+([a-z]*)_v(\d+)$') {
        # Capture before the inner -match, which would clobber $Matches.
        $series = $Matches[1].ToUpper()
        $suffix = $Matches[2]
        $ver = $Matches[3]
        $s = if ($suffix -match 's') { 'S' } else { '' }
        return "standard$series${s}v${ver}Family"
    }
    return $null
}

# --------------------------------------------------------------------------- #
# Probe: App Service plan compute via ARM preflight validate
# --------------------------------------------------------------------------- #
function Get-AppServiceCapacity {
    param([string]$Sub, [string]$Token, [string]$Region)

    $rg = "rg-techlabs-capprobe-$Region"
    $headers = @{ Authorization = "Bearer $Token"; 'Content-Type' = 'application/json' }
    # Create a throwaway RG for the validation (validate is RG-scoped).
    $rgUri = "https://management.azure.com/subscriptions/$Sub/resourcegroups/$rg`?api-version=2021-04-01"
    try {
        Invoke-RestMethod -Method Put -Uri $rgUri -Headers $headers -Body (@{ location = $Region } | ConvertTo-Json) | Out-Null
    }
    catch {
        return New-Status 'unknown' 'could not create probe resource group'
    }

    $template = @{
        '$schema'      = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
        contentVersion = '1.0.0.0'
        resources      = @(
            @{
                type       = 'Microsoft.Web/serverfarms'
                apiVersion = '2023-12-01'
                name       = 'capprobe-plan'
                location   = $Region
                sku        = @{ name = 'B1'; capacity = 1 }
                kind       = 'linux'
                properties = @{ reserved = $true }
            }
        )
    }
    $body = @{ properties = @{ mode = 'Incremental'; template = $template; parameters = @{} } } | ConvertTo-Json -Depth 30
    $valUri = "https://management.azure.com/subscriptions/$Sub/resourceGroups/$rg/providers/Microsoft.Resources/deployments/capprobe/validate?api-version=2021-04-01"

    $result = New-Status 'unknown' 'validation inconclusive'
    try {
        Invoke-RestMethod -Method Post -Uri $valUri -Headers $headers -Body $body | Out-Null
        $result = New-Status 'green' 'B1 plan passes preflight'
    }
    catch {
        $msg = ''
        try { $msg = ($_.ErrorDetails.Message) } catch { $msg = $_.Exception.Message }
        if ($msg -match 'OverQuota|quota|SkuNotAvailable|NotAvailableForSubscription') {
            $result = New-Status 'red' 'no App Service quota in this region'
        }
        elseif ($msg -match 'LocationNotAvailable|not available') {
            $result = New-Status 'red' 'App Service not offered in this region'
        }
        else {
            $result = New-Status 'yellow' 'preflight returned a non-quota error'
        }
    }
    finally {
        # Best-effort async cleanup of the throwaway RG.
        try { Invoke-RestMethod -Method Delete -Uri $rgUri -Headers $headers | Out-Null } catch { }
    }
    return $result
}

# --------------------------------------------------------------------------- #
# Probe: resource-provider regional availability (for services without a simple
# quota API - a region either offers the service or it doesn't).
# --------------------------------------------------------------------------- #
function Get-RegionAvailabilityMap {
    param(
        [string]$Namespace, [string]$ResourceType, [string[]]$Regions,
        [string]$OfferedStatus = 'green', [string]$OfferedDetail = 'offered in this region'
    )
    $q = "resourceTypes[?resourceType=='$ResourceType'].locations | [0]"
    $json = Invoke-Az @('provider', 'show', '--namespace', $Namespace, '--query', $q, '-o', 'json')
    $map = [ordered]@{}
    $locs = $null
    if ($json) { try { $locs = $json | ConvertFrom-Json } catch { $locs = $null } }
    if ($null -eq $locs) {
        foreach ($r in $Regions) { $map[$r] = New-Status 'unknown' 'availability query failed' }
        return $map
    }
    # RP locations are display names ("Brazil South"); normalize to region codes.
    $offered = @{}
    foreach ($l in @($locs)) { $offered[(("$l" -replace '\s', '').ToLower())] = $true }
    foreach ($r in $Regions) {
        if ($offered.ContainsKey($r.ToLower())) { $map[$r] = New-Status $OfferedStatus $OfferedDetail }
        else { $map[$r] = New-Status 'red' 'not offered in this region' }
    }
    return $map
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
$sub = Get-SubscriptionId
Write-Host "Measuring capacity in subscription $sub across: $($Regions -join ', ')" -ForegroundColor Cyan
$token = Get-AccessToken

$services = [ordered]@{}

# Azure OpenAI models
if (-not $SkipOpenAi) {
    if (-not $OpenAiModels -or $OpenAiModels.Count -eq 0) {
        $OpenAiModels = Get-CatalogOpenAiModels
        Write-Host "  derived OpenAI models from catalog: $($OpenAiModels -join ', ')" -ForegroundColor DarkGray
    }
    # Per-region model/SKU deprecation lookup, fetched once and reused across
    # models. $null for a region means the query failed (deprecation unknown).
    $deprecationByRegion = @{}
    foreach ($r in $Regions) {
        $deprecationByRegion[$r] = Get-RegionModelDeprecations -Sub $sub -Token $token -Region $r
    }
    $now = (Get-Date).ToUniversalTime()

    foreach ($spec in $OpenAiModels) {
        $name, $ver, $sku = $spec.Split(':', 3)
        if (-not $sku) { $sku = 'Standard' }
        Write-Host "  OpenAI $name ($ver) [$sku] ..." -ForegroundColor DarkGray
        $probe = Get-OpenAiCapacity -Sub $sub -Token $token -ModelName $name -ModelVersion $ver -SkuName $sku
        $byRegion = $probe.byRegion
        $regionMap = [ordered]@{}
        foreach ($r in $Regions) {
            if ($byRegion.ContainsKey($r)) {
                $regionMap[$r] = $byRegion[$r]
            }
            elseif (-not $probe.ok) {
                $regionMap[$r] = New-Status 'unknown' 'capacity query failed'
            }
            else {
                # API responded but this region has no entry for the deployed SKU.
                $regionMap[$r] = New-Status 'red' "$sku SKU not offered for $name in this region"
            }

            # Veto on lifecycle: even with TPM capacity, Azure refuses NEW
            # deployments of a model whose lifecycleStatus is Deprecating or
            # Deprecated, and of a SKU whose deprecationDate has passed. Either
            # condition makes the region red so the gallery never offers a
            # combination that would fail at deploy time.
            $depMap = $deprecationByRegion[$r]
            if ($null -ne $depMap) {
                $info = $depMap["$name|$ver|$($sku.ToLower())"]
                if ($info) {
                    $status = "$($info.status)"
                    $depRaw = $info.date
                    $depShort = if ($depRaw) { [datetime]::Parse($depRaw).ToUniversalTime().ToString('yyyy-MM-dd') } else { $null }
                    if ($status -match 'Deprecat') {
                        $suffix = if ($depShort) { " (retires $depShort)" } else { '' }
                        $regionMap[$r] = New-Status 'red' "$name $sku is $status - blocked for new deployments$suffix"
                    }
                    elseif ($depRaw) {
                        $depDate = [datetime]::Parse($depRaw).ToUniversalTime()
                        if ($depDate -le $now) {
                            $regionMap[$r] = New-Status 'red' "$name $sku deprecated for new deployments ($depShort)"
                        }
                    }
                }
            }
        }
        $services["openai:${name}:${ver}:$($sku.ToLower())"] = [ordered]@{
            label    = "Azure OpenAI - $name $ver [$sku]"
            provider = 'Microsoft.CognitiveServices/accounts/deployments'
            kind     = 'model-tpm'
            model    = $name
            version  = $ver
            sku      = $sku
            byRegion = $regionMap
        }
    }
}

# App Service
if (-not $SkipAppService) {
    $regionMap = [ordered]@{}
    foreach ($r in $Regions) {
        Write-Host "  App Service preflight $r ..." -ForegroundColor DarkGray
        $regionMap[$r] = Get-AppServiceCapacity -Sub $sub -Token $token -Region $r
    }
    $services['appservice'] = [ordered]@{
        label    = 'Azure App Service (B1+)'
        provider = 'Microsoft.Web/serverFarms'
        kind     = 'compute'
        byRegion = $regionMap
    }
}

# Virtual Machines: total regional cores + per-catalog-VM-size family quota.
if (-not $SkipVm) {
    # Catalog VM sizes offered in the deploy dropdowns (from the registry).
    $vmSizes = @('Standard_D2s_v5', 'Standard_D4s_v5', 'Standard_D8s_v5', 'Standard_D16s_v5', 'Standard_D2s_v6', 'Standard_D4s_v6', 'Standard_D8s_v6', 'Standard_E4s_v5', 'Standard_E8s_v5', 'Standard_F4s_v2', 'Standard_F8s_v2')
    $regPath = Join-Path $RepoRoot 'site/registry.json'
    if (Test-Path $regPath) {
        try {
            $rv = @((Get-Content $regPath -Raw | ConvertFrom-Json).skus.vmSize.values | ForEach-Object { $_.value })
            if ($rv.Count) { $vmSizes = $rv }
        }
        catch { }
    }

    $usageByRegion = @{}
    foreach ($r in $Regions) {
        Write-Host "  VM usage $r ..." -ForegroundColor DarkGray
        $usageByRegion[$r] = Get-VmUsage -Sub $sub -Token $token -Region $r
    }

    # Total regional vCPUs.
    $coreMap = [ordered]@{}
    foreach ($r in $Regions) {
        $u = $usageByRegion[$r]
        if (-not $u) { $coreMap[$r] = New-Status 'unknown' 'usage query failed'; continue }
        $entry = $u | Where-Object { $_.name.value -eq 'cores' } | Select-Object -First 1
        $coreMap[$r] = Get-CoreStatus -Entry $entry -What 'regional'
    }
    $services['vm'] = [ordered]@{
        label    = 'Virtual Machine cores (regional)'
        provider = 'Microsoft.Compute/virtualMachines'
        kind     = 'compute'
        byRegion = $coreMap
    }

    # Per-size family quota, so a chosen VM size checks its own family limit.
    foreach ($size in $vmSizes) {
        $family = Convert-VmSizeToFamily -Size $size
        if (-not $family) { continue }
        $sizeMap = [ordered]@{}
        foreach ($r in $Regions) {
            $u = $usageByRegion[$r]
            if (-not $u) { $sizeMap[$r] = New-Status 'unknown' 'usage query failed'; continue }
            $fe = $u | Where-Object { "$($_.name.value)".ToLower() -eq $family.ToLower() } | Select-Object -First 1
            $sizeMap[$r] = Get-CoreStatus -Entry $fe -What $size
        }
        $services["vm:$size"] = [ordered]@{
            label    = "Virtual Machine - $size"
            provider = 'Microsoft.Compute/virtualMachines'
            kind     = 'compute'
            family   = $family
            byRegion = $sizeMap
        }
    }
}

# Container Apps (availability by region; Consumption has no blocking vCPU quota).
if (-not $SkipContainerApps) {
    Write-Host '  Container Apps availability ...' -ForegroundColor DarkGray
    $services['containerapps'] = [ordered]@{
        label    = 'Azure Container Apps'
        provider = 'Microsoft.App/managedEnvironments'
        kind     = 'availability'
        byRegion = Get-RegionAvailabilityMap -Namespace 'Microsoft.App' -ResourceType 'managedEnvironments' -Regions $Regions
    }
}

# Azure VMware Solution (offered in few regions; node quota is 0 by default, so
# "offered" is yellow - request AVS host quota before deploying).
if (-not $SkipAvs) {
    Write-Host '  AVS availability ...' -ForegroundColor DarkGray
    $services['avs'] = [ordered]@{
        label    = 'Azure VMware Solution'
        provider = 'Microsoft.AVS/privateClouds'
        kind     = 'availability'
        byRegion = Get-RegionAvailabilityMap -Namespace 'Microsoft.AVS' -ResourceType 'privateClouds' -Regions $Regions -OfferedStatus 'yellow' -OfferedDetail 'offered - request AVS host quota (0 by default)'
    }
}

# Microsoft Fabric (capacity SKU availability; tenant entitlement is separate).
if (-not $SkipFabric) {
    Write-Host '  Fabric availability ...' -ForegroundColor DarkGray
    $services['fabric'] = [ordered]@{
        label    = 'Microsoft Fabric'
        provider = 'Microsoft.Fabric/capacities'
        kind     = 'availability'
        byRegion = Get-RegionAvailabilityMap -Namespace 'Microsoft.Fabric' -ResourceType 'capacities' -Regions $Regions -OfferedDetail 'offered - tenant entitlement required'
    }
}

# Power BI Dedicated (capacity SKU availability by region).
if (-not $SkipPowerBi) {
    Write-Host '  Power BI Dedicated availability ...' -ForegroundColor DarkGray
    $services['powerbi'] = [ordered]@{
        label    = 'Power BI Dedicated'
        provider = 'Microsoft.PowerBIDedicated/capacities'
        kind     = 'availability'
        byRegion = Get-RegionAvailabilityMap -Namespace 'Microsoft.PowerBIDedicated' -ResourceType 'capacities' -Regions $Regions
    }
}

# Microsoft Purview (account availability by region).
if (-not $SkipPurview) {
    Write-Host '  Purview availability ...' -ForegroundColor DarkGray
    $services['purview'] = [ordered]@{
        label    = 'Microsoft Purview'
        provider = 'Microsoft.Purview/accounts'
        kind     = 'availability'
        byRegion = Get-RegionAvailabilityMap -Namespace 'Microsoft.Purview' -ResourceType 'accounts' -Regions $Regions
    }
}

$capacity = [ordered]@{
    generated      = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    subscriptionId = $sub
    regions        = $Regions
    services       = $services
    legend         = [ordered]@{
        green   = 'Capacity available'
        yellow  = 'Limited or unknown - may hit constraints'
        red     = 'No capacity - request a quota increase or pick another region'
        unknown = 'Could not determine'
    }
}

$dir = Split-Path -Parent $OutFile
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$capacity | ConvertTo-Json -Depth 10 | Set-Content -Path $OutFile -Encoding UTF8
Write-Host "Wrote $OutFile" -ForegroundColor Green

