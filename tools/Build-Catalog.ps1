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

    $filterParts = @("armRegionName eq '$Region'", "priceType eq 'Consumption'")
    if ($Component.serviceName) { $filterParts += "serviceName eq '$($Component.serviceName)'" }
    if ($Component.armSkuName) { $filterParts += "armSkuName eq '$($Component.armSkuName)'" }
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
            $resp = Invoke-RestMethod -Uri $url -Method Get
            foreach ($i in $resp.Items) { $items.Add($i) }
            $url = $resp.NextPageLink
            $page++
        } while ($url -and $page -lt 20)
        $script:PriceCache[$filter] = $items
    }

    $cand = $items
    if ($Component.skuNameContains) { $cand = $cand | Where-Object { $_.skuName -like "*$($Component.skuNameContains)*" } }
    if ($Component.productNameContains) { $cand = $cand | Where-Object { $_.productName -like "*$($Component.productNameContains)*" } }
    if ($Component.meterNameContains) { $cand = $cand | Where-Object { $_.meterName -like "*$($Component.meterNameContains)*" } }
    $cand = $cand | Where-Object { $_.retailPrice -gt 0 }

    if (-not $cand) { return $null }
    return $cand | Sort-Object retailPrice | Select-Object -First 1
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

        $components.Add([ordered]@{
            name             = $c.name
            billing          = $c.billing
            serviceName      = $c.serviceName
            unitOfMeasure    = $c.unitOfMeasure
            quantityPerMonth = $c.quantityPerMonth
            notes            = $c.notes
            unitPrice        = $unitPrice
            monthly          = $monthly
        })
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

$metaFiles = Get-ChildItem -Path $CatalogDir -Recurse -Filter 'metadata.json' -File | Sort-Object FullName
$items = New-Object System.Collections.Generic.List[object]

foreach ($mf in $metaFiles) {
    $meta = Get-Content $mf.FullName -Raw | ConvertFrom-Json
    $rel = $mf.DirectoryName.Substring($CatalogDir.Length).TrimStart('\', '/').Replace('\', '/')

    $componentCount = if ($meta.cost -and $meta.cost.components) { @($meta.cost.components).Count } else { 0 }
    $hasProd = Test-Path (Join-Path $mf.DirectoryName 'parameters/prod.bicepparam')
    $costObj = if ($meta.cost) { Get-PricedCost -Cost $meta.cost } else { $null }
    if ($costObj) { Write-Host ("  priced {0}: {1} {2}/mo" -f $rel, $costObj.currency, $costObj.totalMonthly) -ForegroundColor DarkGray }

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
        path             = $rel
        readme           = "catalog/$rel/README.md"
        template         = "catalog/$rel/main.bicep"
        hasProd          = $hasProd
        componentCount   = $componentCount
        costProfile      = if ($meta.cost) { $meta.cost.profile } else { $null }
        cost             = $costObj
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
