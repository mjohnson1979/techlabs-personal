<#
.SYNOPSIS
    Generate site/catalog.json from every catalog use case's metadata.json.

.DESCRIPTION
    Scans catalog/**/metadata.json and emits a single catalog.json the static
    SME&C Techlabs gallery (site/index.html) loads at runtime. Run this whenever
    you add or change a use case's metadata so the gallery stays in sync.

.EXAMPLE
    ./tools/Build-Catalog.ps1
#>
[CmdletBinding()]
param(
    [string]$OutFile,

    # Region/currency used to price cost components against the public
    # Azure Retail Prices API (no Azure login required).
    [string]$Region = 'eastus',
    [string]$Currency = 'USD',

    # Skip live pricing (offline/fast build). catalog.json is still written,
    # but cost components carry no dollar figures.
    [switch]$SkipPricing
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$CatalogDir = Join-Path $RepoRoot 'catalog'
if (-not $OutFile) { $OutFile = Join-Path $RepoRoot 'site/catalog.json' }

$PricesApi = 'https://prices.azure.com/api/retail/prices'
$script:PriceCache = @{}

function Get-RetailPrice {
    param([Parameter(Mandatory)] $Component)

    # Note: armSkuName is intentionally NOT pushed into the server-side OData
    # filter. Metadata often uses friendly SKU names (e.g. 'P1v3') that do not
    # equal the API's region-specific armSkuName (e.g. 'Standard_P1_v3_Windows'),
    # so an exact match returns nothing. We fetch by serviceName and match the
    # SKU loosely client-side below. Global services (Front Door, DNS, etc.) are
    # priced under armRegionName 'Global', so we retry there when the regional
    # query yields nothing.
    foreach ($regionName in @($Region, 'Global')) {
        $filterParts = @("armRegionName eq '$regionName'", "priceType eq 'Consumption'")
        if ($Component.serviceName) { $filterParts += "serviceName eq '$($Component.serviceName)'" }
        # For very large services (e.g. Virtual Machines) the 40-page cap can
        # truncate before the wanted SKU. When metadata supplies an exact meter
        # name, push it server-side so the result set is tiny and reliable.
        if ($Component.meterNameExact) { $filterParts += "meterName eq '$($Component.meterNameExact)'" }
        $filter = $filterParts -join ' and '

        if ($script:PriceCache.ContainsKey($filter)) {
            $items = $script:PriceCache[$filter]
        }
        else {
            $escaped = [uri]::EscapeDataString($filter)
            $url = "{0}?currencyCode={1}&`$filter={2}" -f $PricesApi, $Currency, $escaped
            $items = New-Object System.Collections.Generic.List[object]
            $page = 0
            do {
                $resp = $null
                for ($attempt = 0; $attempt -lt 5; $attempt++) {
                    try { $resp = Invoke-RestMethod -Uri $url -Method Get; break }
                    catch {
                        if ($_.Exception.Response.StatusCode.value__ -eq 429) {
                            Start-Sleep -Seconds ([math]::Pow(2, $attempt))
                        }
                        else { throw }
                    }
                }
                if (-not $resp) { break }
                foreach ($i in $resp.Items) { $items.Add($i) }
                $url = $resp.NextPageLink
                $page++
            } while ($url -and $page -lt 40)
            $script:PriceCache[$filter] = $items
        }

        $cand = $items
        if ($Component.skuNameContains) { $cand = $cand | Where-Object { $_.skuName -like "*$($Component.skuNameContains)*" } }
        if ($Component.productNameContains) { $cand = $cand | Where-Object { $_.productName -like "*$($Component.productNameContains)*" } }
        if ($Component.meterNameContains) { $cand = $cand | Where-Object { $_.meterName -like "*$($Component.meterNameContains)*" } }
        if ($Component.armSkuName) {
            $want = ($Component.armSkuName -replace '[^a-zA-Z0-9]', '').ToLowerInvariant()
            $cand = $cand | Where-Object {
                $hay = (@($_.armSkuName, $_.skuName, $_.meterName) -join ' ') -replace '[^a-zA-Z0-9]', ''
                $hay.ToLowerInvariant().Contains($want)
            }
        }
        # Skip reserved/spot/discounted/dev-test rows so the cheapest-match
        # heuristic below reflects pay-as-you-go list price.
        $cand = $cand | Where-Object {
            $blob = "$($_.productName) $($_.skuName) $($_.meterName)"
            $blob -notmatch '(?i)discount|reserv|spot|low priority|dev[ /-]?test'
        }
        $cand = @($cand | Where-Object { $_.retailPrice -gt 0 })

        # If the requested meter name matches a meter exactly, prefer those rows
        # so sub-meters (e.g. "P10 LRS Disk Mount") don't win the cheapest sort.
        if ($Component.meterNameContains) {
            $exact = @($cand | Where-Object { $_.meterName -eq $Component.meterNameContains })
            if ($exact.Count) { $cand = $exact }
        }

        if ($cand.Count) { return $cand | Sort-Object retailPrice | Select-Object -First 1 }
    }
    return $null
}

function Get-PricedCost {
    param([Parameter(Mandatory)] $Cost)

    $components = New-Object System.Collections.Generic.List[object]
    $fixedTotal = 0.0
    $usageTotal = 0.0
    $allPriced = $true

    foreach ($c in @($Cost.components)) {
        $qty = if ($null -ne $c.quantityPerMonth) { [double]$c.quantityPerMonth } else { 0 }
        $unitPrice = $null
        $monthly = $null
        if (-not $SkipPricing) {
            try {
                $price = Get-RetailPrice -Component $c
                if ($null -ne $price) {
                    $unitPrice = [math]::Round($price.retailPrice, 5)
                    $monthly = [math]::Round($price.retailPrice * $qty, 2)
                }
            }
            catch {
                Write-Warning "Pricing failed for '$($c.name)': $($_.Exception.Message)"
            }
        }
        if ($null -eq $monthly) { $allPriced = $false }
        elseif ($c.billing -eq 'fixed') { $fixedTotal += $monthly }
        else { $usageTotal += $monthly }

        $entry = [ordered]@{
            name             = $c.name
            billing          = $c.billing
            serviceName      = $c.serviceName
            unitOfMeasure    = $c.unitOfMeasure
            quantityPerMonth = $c.quantityPerMonth
            notes            = $c.notes
            unitPrice        = $unitPrice
            monthly          = $monthly
        }
        # Carry the price-match + override-annotation fields so the backend can
        # re-price live (POST /api/estimate) for the selected region/parameters.
        foreach ($f in @('armSkuName', 'productNameContains', 'meterNameContains', 'meterNameExact', 'skuNameContains', 'skuParam', 'instanceParam', 'hoursPerInstance', 'zoneRedundantParam', 'zoneRedundantMinInstances')) {
            if ($null -ne $c.$f) { $entry[$f] = $c.$f }
        }
        $components.Add($entry)
    }

    $priced = -not $SkipPricing
    return [ordered]@{
        currency      = if ($Cost.currency) { $Cost.currency } else { $Currency }
        region        = if ($Cost.region) { $Cost.region } else { $Region }
        profile       = $Cost.profile
        components    = $components
        fixedMonthly  = if ($priced) { [math]::Round($fixedTotal, 2) } else { $null }
        usageMonthly  = if ($priced) { [math]::Round($usageTotal, 2) } else { $null }
        totalMonthly  = if ($priced) { [math]::Round($fixedTotal + $usageTotal, 2) } else { $null }
        complete      = $priced -and $allPriced
        pricedAt      = if ($priced) { (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') } else { $null }
    }
}

# Extract user-facing IaC parameters from a use case's main.bicep by compiling
# it to ARM JSON (robust vs. regex). Skips params already surfaced by dedicated
# UI controls. Returns an array of ordered hashtables, or empty if unavailable.
function Get-BicepParameters {
    param([Parameter(Mandatory)][string]$BicepPath)

    if (-not (Test-Path $BicepPath)) { return @() }
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("bicep-params-" + [guid]::NewGuid().ToString('N') + ".json")
    try {
        az bicep build --file $BicepPath --outfile $tmp --only-show-errors 2>$null | Out-Null
        if (-not (Test-Path $tmp)) { return @() }
        $json = Get-Content $tmp -Raw | ConvertFrom-Json
    }
    catch {
        Write-Warning "Could not compile $BicepPath for parameters: $($_.Exception.Message)"
        return @()
    }
    finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
    if (-not $json -or -not $json.parameters) { return @() }

    $skip = @('location', 'environment')
    $params = New-Object System.Collections.Generic.List[object]
    foreach ($prop in $json.parameters.PSObject.Properties) {
        if ($skip -contains $prop.Name) { continue }
        $p = $prop.Value
        $entry = [ordered]@{
            name        = $prop.Name
            type        = $p.type
            default     = $p.defaultValue
            description = if ($p.metadata) { $p.metadata.description } else { $null }
        }
        if ($null -ne $p.allowedValues) { $entry.allowed = @($p.allowedValues) }
        if ($null -ne $p.minValue) { $entry.min = $p.minValue }
        if ($null -ne $p.maxValue) { $entry.max = $p.maxValue }
        $params.Add($entry)
    }
    return $params.ToArray()
}

$metaFiles = Get-ChildItem -Path $CatalogDir -Recurse -Filter 'metadata.json' -File | Sort-Object FullName
$items = New-Object System.Collections.Generic.List[object]

foreach ($mf in $metaFiles) {
    $meta = Get-Content $mf.FullName -Raw | ConvertFrom-Json
    $rel = $mf.DirectoryName.Substring($CatalogDir.Length).TrimStart('\', '/').Replace('\', '/')

    $componentCount = if ($meta.cost -and $meta.cost.components) { @($meta.cost.components).Count } else { 0 }
    $hasProd = Test-Path (Join-Path $mf.DirectoryName 'parameters/prod.bicepparam')
    $hasTerraform = Test-Path (Join-Path $mf.DirectoryName 'terraform/main.tf')
    $engines = New-Object System.Collections.Generic.List[string]
    $engines.Add('bicep')
    if ($hasTerraform) { $engines.Add('terraform') }
    $costObj = if ($meta.cost) { Get-PricedCost -Cost $meta.cost } else { $null }
    if ($costObj) { Write-Host ("  priced {0}: {1} {2}/mo" -f $rel, $costObj.currency, $costObj.totalMonthly) -ForegroundColor DarkGray }

    $parameters = Get-BicepParameters -BicepPath (Join-Path $mf.DirectoryName 'main.bicep')

    $items.Add([ordered]@{
        name             = $meta.name
        displayName      = $meta.displayName
        category         = if ($meta.category) { $meta.category } else { 'Other' }
        description      = $meta.description
        what             = $meta.what
        why              = $meta.why
        potentialUseCases = @($meta.potentialUseCases)
        industries       = @($meta.industries)
        compliance       = @($meta.compliance)
        wellArchitected  = $meta.wellArchitected
        quota            = $meta.quota
        architecture     = $meta.architecture
        path             = $rel
        readme           = "catalog/$rel/README.md"
        template         = "catalog/$rel/main.bicep"
        templateTf       = if ($hasTerraform) { "catalog/$rel/terraform" } else { $null }
        engines          = $engines.ToArray()
        hasProd          = $hasProd
        componentCount   = $componentCount
        costProfile      = if ($meta.cost) { $meta.cost.profile } else { $null }
        cost             = $costObj
        parameters       = $parameters
    })
}

$catalog = [ordered]@{
    name        = 'SME&C Techlabs'
    description = 'Templatized Azure use cases as Infrastructure as Code - resilient, Well-Architected, with built-in cost estimation.'
    generated   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    count       = $items.Count
    items       = $items
}

$dir = Split-Path -Parent $OutFile
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$catalog | ConvertTo-Json -Depth 10 | Set-Content -Path $OutFile -Encoding UTF8

Write-Host "Wrote $($items.Count) use cases to $OutFile" -ForegroundColor Green
