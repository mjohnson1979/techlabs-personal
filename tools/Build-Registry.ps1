<#
.SYNOPSIS
    Build site/registry.json: the live catalog of deployable models and service
    SKUs (with region availability) that the gallery's advanced deployment
    options offer as dropdowns.

.DESCRIPTION
    The advanced deployment options let a user override template parameters. For
    parameters that pick a model or a service tier, the choices should reflect
    what Azure actually offers today - not a hand-maintained list that drifts as
    Microsoft adds providers (OpenAI, Anthropic, xAI, Meta, Mistral, ...) and
    retires SKUs. This script snapshots that surface into site/registry.json,
    refreshed on a schedule (see .github/workflows/registry.yml).

    Sources:
      * Models      - Microsoft.CognitiveServices .../locations/{loc}/models.
                      Every provider/format, version, deployment SKU, plus
                      lifecycleStatus and deprecation dates (retired models and
                      SKUs are dropped).
      * VM sizes    - Microsoft.Compute/skus (region availability + restrictions).
      * Storage     - Microsoft.Storage/skus (region availability).
      * Fixed tiers - services whose SKUs are a fixed enum with no per-region SKU
                      API (App Service plans, API Management, Service Bus, Event
                      Hubs, AI Search, Front Door, Databricks, Logic Apps, ...)
                      are curated here and treated as available in every region.

    Auth mirrors Build-Capacity.ps1: az CLI for the ARM token, then REST. Run
    `az login` (or rely on the workflow's OIDC login) before invoking.

.EXAMPLE
    ./tools/Build-Registry.ps1

.EXAMPLE
    ./tools/Build-Registry.ps1 -SkipLive   # curated SKUs only, no Azure calls
#>
[CmdletBinding()]
param(
    [string]$OutFile,

    [string]$SubscriptionId,

    # Americas public regions (GA; canary/partner regions excluded). Model and
    # VM/storage availability is probed across these.
    [string[]]$Regions = @(
        'eastus', 'eastus2', 'centralus', 'northcentralus', 'southcentralus',
        'westus', 'westus2', 'westus3', 'westcentralus',
        'canadacentral', 'canadaeast',
        'brazilsouth', 'brazilsoutheast',
        'mexicocentral'
    ),

    # Skip all Azure calls and emit only the curated (fixed-tier) SKUs. Useful
    # for offline template/UI work.
    [switch]$SkipLive
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutFile) { $OutFile = Join-Path $RepoRoot 'site/registry.json' }

# --------------------------------------------------------------------------- #
# Azure CLI / ARM helpers (same approach as Build-Capacity.ps1)
# --------------------------------------------------------------------------- #
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

function Invoke-Arm {
    param([string]$Uri, [string]$Token)
    $headers = @{ Authorization = "Bearer $Token" }
    $items = New-Object System.Collections.Generic.List[object]
    $next = $Uri
    while ($next) {
        $resp = Invoke-RestMethod -Method Get -Uri $next -Headers $headers
        if ($resp.value) { foreach ($v in $resp.value) { $items.Add($v) } }
        $next = $resp.nextLink
    }
    return $items
}

# A SKU/model deprecation date in the past means it is retired: drop it.
function Test-Retired {
    param($DateValue)
    if (-not $DateValue) { return $false }
    try { return ([datetime]$DateValue) -lt (Get-Date) } catch { return $false }
}

# --------------------------------------------------------------------------- #
# Curated fixed-tier service SKUs (no per-region SKU API; available everywhere
# in the Americas regions we scan). Keep these aligned with the catalog's
# main.bicep @allowed sets.
# --------------------------------------------------------------------------- #
$CuratedSkus = [ordered]@{
    appServiceSku = [ordered]@{ label = 'App Service plan'; values = @('B1', 'S1', 'P0v3', 'P1v3', 'P2v3', 'P3v3') }
    apimSku       = [ordered]@{ label = 'API Management'; values = @('Developer', 'Basic', 'Standard', 'Premium', 'BasicV2', 'StandardV2') }
    searchSku     = [ordered]@{ label = 'AI Search'; values = @('free', 'basic', 'standard', 'standard2', 'standard3') }
    serviceBusSku = [ordered]@{ label = 'Service Bus'; values = @('Basic', 'Standard', 'Premium') }
    eventHubsSku  = [ordered]@{ label = 'Event Hubs'; values = @('Basic', 'Standard', 'Premium') }
    functionPlanSku = [ordered]@{ label = 'Functions Elastic Premium'; values = @('EP1', 'EP2', 'EP3') }
    workflowSku   = [ordered]@{ label = 'Logic Apps Standard'; values = @('WS1', 'WS2', 'WS3') }
    databricksSku = [ordered]@{ label = 'Azure Databricks'; values = @('standard', 'premium') }
    frontDoorSku  = [ordered]@{ label = 'Front Door'; values = @('Standard_AzureFrontDoor', 'Premium_AzureFrontDoor') }
    linuxFxVersion = [ordered]@{ label = 'Linux runtime stack'; values = @('DOTNETCORE|8.0', 'DOTNETCORE|9.0', 'NODE|20-lts', 'NODE|22-lts', 'PYTHON|3.12', 'PYTHON|3.13', 'JAVA|21-java21') }
    kubernetesVersion = [ordered]@{ label = 'Kubernetes version'; values = @('1.28', '1.29', '1.30', '1.31', '1.32') }
    avsSkuName    = [ordered]@{ label = 'AVS host SKU'; values = @('AV36', 'AV36P', 'AV52', 'AV64') }
    sqlTier       = [ordered]@{ label = 'Azure SQL tier (vCore)'; values = @('GeneralPurpose', 'BusinessCritical', 'Hyperscale') }
    sqlSkuName    = [ordered]@{ label = 'Azure SQL SKU'; values = @('GP_S_Gen5_1', 'GP_S_Gen5_2', 'GP_S_Gen5_4', 'GP_S_Gen5_8', 'GP_Gen5_2', 'GP_Gen5_4', 'GP_Gen5_8', 'BC_Gen5_2', 'BC_Gen5_4', 'BC_Gen5_8', 'HS_Gen5_2', 'HS_Gen5_4', 'HS_Gen5_8') }
    # A curated shortlist of general-purpose VM sizes (the live probe annotates
    # per-region availability). The full Compute SKU list is too large for a picker.
    vmSize        = [ordered]@{ label = 'Virtual machine size'; values = @('Standard_D2s_v5', 'Standard_D4s_v5', 'Standard_D8s_v5', 'Standard_D16s_v5', 'Standard_D2s_v6', 'Standard_D4s_v6', 'Standard_D8s_v6', 'Standard_E4s_v5', 'Standard_E8s_v5', 'Standard_F4s_v2', 'Standard_F8s_v2') }
}

# Aliases: catalog params that reuse another service's SKU family.
$SkuAliases = [ordered]@{
    backendSku    = 'appServiceSku'   # api-management-gateway backend App Service
    lakeStorageSku = 'storageSku'     # data-lakehouse storage (filled from live storage)
}

# Curated OpenAI model seed. Guarantees the gallery has model dropdowns offline
# and before the first live refresh; the live probe below replaces this whenever
# it returns results. All are OpenAI-format (the format the templates deploy).
function Get-SeedModels {
    $chat = @(
        [ordered]@{ provider = 'OpenAI'; name = 'gpt-5'; version = '2025-08-07'; skus = @('GlobalStandard', 'DataZoneStandard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'chat' }
        [ordered]@{ provider = 'OpenAI'; name = 'gpt-5-mini'; version = '2025-08-07'; skus = @('GlobalStandard', 'DataZoneStandard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'chat' }
        [ordered]@{ provider = 'OpenAI'; name = 'gpt-5-nano'; version = '2025-08-07'; skus = @('GlobalStandard', 'DataZoneStandard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'chat' }
        [ordered]@{ provider = 'OpenAI'; name = 'gpt-4.1'; version = '2025-04-14'; skus = @('GlobalStandard', 'DataZoneStandard', 'Standard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'chat' }
        [ordered]@{ provider = 'OpenAI'; name = 'gpt-4o'; version = '2024-11-20'; skus = @('GlobalStandard', 'DataZoneStandard', 'Standard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'chat' }
        [ordered]@{ provider = 'OpenAI'; name = 'gpt-4o-mini'; version = '2024-07-18'; skus = @('GlobalStandard', 'DataZoneStandard', 'Standard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'chat' }
    )
    $embedding = @(
        [ordered]@{ provider = 'OpenAI'; name = 'text-embedding-3-large'; version = '1'; skus = @('Standard', 'GlobalStandard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'embedding' }
        [ordered]@{ provider = 'OpenAI'; name = 'text-embedding-3-small'; version = '1'; skus = @('Standard', 'GlobalStandard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'embedding' }
        [ordered]@{ provider = 'OpenAI'; name = 'text-embedding-ada-002'; version = '2'; skus = @('Standard'); regions = 'all'; lifecycle = 'GenerallyAvailable'; kind = 'embedding' }
    )
    return [ordered]@{ chat = $chat; embedding = $embedding }
}

# --------------------------------------------------------------------------- #
# Live probe: Cognitive Services model catalog (all providers) per region.
# --------------------------------------------------------------------------- #
function Get-ModelCatalog {
    param([string]$Sub, [string]$Token, [string[]]$Regions)

    # Aggregate across regions: key = format|name|version -> entry with region set.
    $byKey = @{}
    foreach ($region in $Regions) {
        $uri = "https://management.azure.com/subscriptions/$Sub/providers/Microsoft.CognitiveServices/locations/$region/models?api-version=2024-10-01"
        try { $models = Invoke-Arm -Uri $uri -Token $Token }
        catch { Write-Warning "models probe failed for ${region}: $($_.Exception.Message)"; continue }

        foreach ($m in $models) {
            $model = $m.model
            if (-not $model) { continue }
            $status = "$($model.lifecycleStatus)"
            if ($status -and ($status -match 'Deprecat|Retir')) { continue }
            if (Test-Retired $model.deprecation.inference) { continue }

            $format = "$($model.format)"
            $name = "$($model.name)"
            $version = "$($model.version)"
            if (-not $name) { continue }

            $skuNames = New-Object System.Collections.Generic.List[string]
            foreach ($s in @($model.skus)) {
                if (Test-Retired $s.deprecationDate) { continue }
                if ($s.name) { $skuNames.Add("$($s.name)") }
            }

            $caps = $model.capabilities
            $isChat = $false; $isEmbed = $false
            if ($caps) {
                if ("$($caps.chatCompletion)" -eq 'true' -or "$($caps.completion)" -eq 'true') { $isChat = $true }
                if ("$($caps.embeddings)" -eq 'true') { $isEmbed = $true }
            }
            if (-not $isChat -and -not $isEmbed) {
                if ($name -match 'embed') { $isEmbed = $true } else { $isChat = $true }
            }

            $key = "$format|$name|$version"
            if (-not $byKey.ContainsKey($key)) {
                $byKey[$key] = [ordered]@{
                    provider  = $format
                    name      = $name
                    version   = $version
                    skus      = @($skuNames | Sort-Object -Unique)
                    regions   = New-Object System.Collections.Generic.List[string]
                    lifecycle = $status
                    kind      = if ($isEmbed) { 'embedding' } else { 'chat' }
                }
            }
            $byKey[$key].regions.Add($region)
        }
    }

    $chat = New-Object System.Collections.Generic.List[object]
    $embedding = New-Object System.Collections.Generic.List[object]
    foreach ($entry in $byKey.Values) {
        $entry.regions = @($entry.regions | Sort-Object -Unique)
        if ($entry.kind -eq 'embedding') { $embedding.Add($entry) } else { $chat.Add($entry) }
    }
    return [ordered]@{
        chat      = @($chat | Sort-Object { $_.provider }, { $_.name }, { $_.version })
        embedding = @($embedding | Sort-Object { $_.provider }, { $_.name }, { $_.version })
    }
}

# --------------------------------------------------------------------------- #
# Live probe: resource SKUs (Compute VM sizes, Storage) with region availability.
# A SKU is "available" in a region if it is listed there without a hard
# NotAvailableForSubscription/Location restriction.
# --------------------------------------------------------------------------- #
function Get-ResourceSkus {
    param([string]$Sub, [string]$Token, [string]$Provider, [string]$ApiVersion, [string[]]$Regions, [string[]]$ResourceTypes)

    $uri = "https://management.azure.com/subscriptions/$Sub/providers/$Provider/skus?api-version=$ApiVersion"
    try { $skus = Invoke-Arm -Uri $uri -Token $Token }
    catch { Write-Warning "$Provider skus probe failed: $($_.Exception.Message)"; return @{} }

    $regionSet = @{}; foreach ($r in $Regions) { $regionSet[$r.ToLower()] = $true }
    $byName = @{}   # name -> region list
    foreach ($sku in $skus) {
        if ($ResourceTypes -and ($ResourceTypes -notcontains "$($sku.resourceType)")) { continue }
        $name = "$($sku.name)"
        if (-not $name) { continue }

        $restrictedLocs = @{}
        foreach ($res in @($sku.restrictions)) {
            if ("$($res.type)" -eq 'Location') {
                foreach ($loc in @($res.restrictionInfo.locations)) { $restrictedLocs[$loc.ToLower()] = $true }
                foreach ($loc in @($res.values)) { $restrictedLocs[$loc.ToLower()] = $true }
            }
        }
        foreach ($loc in @($sku.locations)) {
            $l = $loc.ToLower()
            if (-not $regionSet.ContainsKey($l)) { continue }
            if ($restrictedLocs.ContainsKey($l)) { continue }
            if (-not $byName.ContainsKey($name)) { $byName[$name] = New-Object System.Collections.Generic.List[string] }
            if ($byName[$name] -notcontains $l) { $byName[$name].Add($l) }
        }
    }
    return $byName
}

function ConvertTo-SkuValues {
    param([hashtable]$ByName)
    $values = New-Object System.Collections.Generic.List[object]
    foreach ($name in ($ByName.Keys | Sort-Object)) {
        $values.Add([ordered]@{ value = $name; regions = @($ByName[$name] | Sort-Object -Unique) })
    }
    return $values.ToArray()
}

# Live probe: non-preview AKS Kubernetes minor versions available per region.
# This endpoint returns a 'values' array (not the usual 'value'), so read directly.
function Get-KubernetesVersions {
    param([string]$Sub, [string]$Token, [string[]]$Regions)
    $headers = @{ Authorization = "Bearer $Token" }
    $byName = @{}
    foreach ($region in $Regions) {
        $uri = "https://management.azure.com/subscriptions/$Sub/providers/Microsoft.ContainerService/locations/$region/kubernetesVersions?api-version=2024-05-01"
        try { $resp = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers }
        catch { continue }
        foreach ($it in @($resp.values)) {
            $ver = "$($it.version)"
            if (-not $ver -or $it.isPreview) { continue }
            if (-not $byName.ContainsKey($ver)) { $byName[$ver] = New-Object System.Collections.Generic.List[string] }
            if ($byName[$ver] -notcontains $region.ToLower()) { $byName[$ver].Add($region.ToLower()) }
        }
    }
    return $byName
}

# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
$models = Get-SeedModels
$skus = [ordered]@{}

# Curated fixed-tier SKUs first (always present, region-agnostic).
foreach ($entry in $CuratedSkus.GetEnumerator()) {
    $name = $entry.Key
    $def = $entry.Value
    $vals = New-Object System.Collections.Generic.List[object]
    foreach ($v in $def['values']) {
        $vals.Add([ordered]@{ value = $v; regions = 'all' })
    }
    $skus[$name] = [ordered]@{ label = $def['label']; source = 'curated'; values = $vals.ToArray() }
}

if (-not $SkipLive) {
    $sub = Get-SubscriptionId
    $token = Get-AccessToken
    Write-Host "Probing subscription $sub across $($Regions.Count) Americas regions..."

    Write-Host '  models (Cognitive Services catalog, all providers)'
    $live = Get-ModelCatalog -Sub $sub -Token $token -Regions $Regions
    if ($live.chat.Count -gt 0 -or $live.embedding.Count -gt 0) { $models = $live }
    Write-Host "    chat=$($models.chat.Count) embedding=$($models.embedding.Count)"

    Write-Host '  VM size availability (Microsoft.Compute/skus, curated shortlist)'
    $vm = Get-ResourceSkus -Sub $sub -Token $token -Provider 'Microsoft.Compute' -ApiVersion '2021-07-01' -Regions $Regions -ResourceTypes @('virtualMachines')
    if ($vm.Count) {
        $refined = New-Object System.Collections.Generic.List[object]
        foreach ($v in $CuratedSkus['vmSize']['values']) {
            if ($vm.ContainsKey($v)) { $refined.Add([ordered]@{ value = $v; regions = @($vm[$v] | Sort-Object -Unique) }) }
            else { $refined.Add([ordered]@{ value = $v; regions = 'all' }) }
        }
        $skus['vmSize'] = [ordered]@{ label = 'Virtual machine size'; source = 'live'; values = $refined.ToArray() }
    }

    Write-Host '  Storage SKUs (Microsoft.Storage/skus)'
    $st = Get-ResourceSkus -Sub $sub -Token $token -Provider 'Microsoft.Storage' -ApiVersion '2023-05-01' -Regions $Regions -ResourceTypes @('storageAccounts')
    if ($st.Count) {
        $storageVals = ConvertTo-SkuValues $st
        $skus['storageSku'] = [ordered]@{ label = 'Storage account'; source = 'live'; values = $storageVals }
    }

    Write-Host '  Kubernetes versions (Microsoft.ContainerService)'
    $k8s = Get-KubernetesVersions -Sub $sub -Token $token -Regions $Regions
    if ($k8s.Count) { $skus['kubernetesVersion'] = [ordered]@{ label = 'Kubernetes version'; source = 'live'; values = ConvertTo-SkuValues $k8s } }
}

# Resolve SKU aliases (params that reuse another family).
foreach ($entry in $SkuAliases.GetEnumerator()) {
    $alias = $entry.Key
    $target = $entry.Value
    if ($skus.Contains($target) -and -not $skus.Contains($alias)) {
        $t = $skus[$target]
        $skus[$alias] = [ordered]@{ label = $t['label']; source = $t['source']; alias = $target; values = $t['values'] }
    }
}

$registry = [ordered]@{
    generated = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    regions   = @($Regions)
    models    = $models
    skus      = $skus
}

$registry | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding UTF8
Write-Host "Wrote registry to $OutFile (models: chat=$($models.chat.Count) embedding=$($models.embedding.Count); sku families=$($skus.Count))"
