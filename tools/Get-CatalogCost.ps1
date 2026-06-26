<#
.SYNOPSIS
    Estimate the monthly cost and total cost of ownership (TCO) of a catalog use case.

.DESCRIPTION
    Reads a use case's metadata.json (the `cost.components` array) and prices every
    component live against the public Azure Retail Prices API
    (https://prices.azure.com/api/retail/prices). No Azure login or subscription is
    required - the retail price list is public.

    Each component is classified as:
      - fixed : always-on resources billed per hour/month (e.g. an App Service plan).
                These are summed into the committed monthly cost.
      - usage : consumption resources billed per unit (e.g. Log Analytics per GB,
                OpenAI per 1K tokens). Priced using the assumed monthly quantity in
                metadata so you get an illustrative - not guaranteed - figure.

    TCO (monthly) = sum(fixed) + sum(estimated usage).

.PARAMETER UseCase
    Folder name under catalog/ (e.g. 'web-app'). Omit and pass -All to price everything.

.PARAMETER All
    Price every use case in the catalog and print a portfolio TCO roll-up.

.PARAMETER Region
    armRegionName used for pricing. Default: eastus.

.PARAMETER Currency
    ISO currency code. Default: USD.

.EXAMPLE
    ./tools/Get-CatalogCost.ps1 -UseCase web-app

.EXAMPLE
    ./tools/Get-CatalogCost.ps1 -All -Region westeurope -Currency EUR
#>
[CmdletBinding(DefaultParameterSetName = 'Single')]
param(
    [Parameter(ParameterSetName = 'Single', Position = 0)]
    [string]$UseCase,

    [Parameter(ParameterSetName = 'All')]
    [switch]$All,

    [string]$Region = 'eastus',

    [string]$Currency = 'USD'
)

$ErrorActionPreference = 'Stop'
$ApiBase = 'https://prices.azure.com/api/retail/prices'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$CatalogDir = Join-Path $RepoRoot 'catalog'

function Get-RetailPrice {
    param(
        [Parameter(Mandatory)] $Component,
        [Parameter(Mandatory)] [string]$Region,
        [Parameter(Mandatory)] [string]$Currency
    )

    $filterParts = @("armRegionName eq '$Region'", "priceType eq 'Consumption'")
    if ($Component.serviceName) { $filterParts += "serviceName eq '$($Component.serviceName)'" }
    if ($Component.armSkuName) { $filterParts += "armSkuName eq '$($Component.armSkuName)'" }
    $filter = $filterParts -join ' and '

    $url = "$ApiBase?currencyCode='$Currency'&`$filter=$([uri]::EscapeDataString($filter))"

    $items = New-Object System.Collections.Generic.List[object]
    do {
        $resp = Invoke-RestMethod -Uri $url -Method Get
        foreach ($i in $resp.Items) { $items.Add($i) }
        $url = $resp.NextPageLink
    } while ($url)

    $cand = $items
    if ($Component.skuNameContains) { $cand = $cand | Where-Object { $_.skuName -like "*$($Component.skuNameContains)*" } }
    if ($Component.productNameContains) { $cand = $cand | Where-Object { $_.productName -like "*$($Component.productNameContains)*" } }
    if ($Component.meterNameContains) { $cand = $cand | Where-Object { $_.meterName -like "*$($Component.meterNameContains)*" } }
    $cand = $cand | Where-Object { $_.retailPrice -gt 0 }

    if (-not $cand) { return $null }
    return $cand | Sort-Object retailPrice | Select-Object -First 1
}

function Get-UseCaseCost {
    param([Parameter(Mandatory)] [string]$Path)

    $metaFile = Join-Path $Path 'metadata.json'
    if (-not (Test-Path $metaFile)) {
        Write-Warning "No metadata.json in $Path - skipping."
        return $null
    }

    $meta = Get-Content $metaFile -Raw | ConvertFrom-Json
    $rows = New-Object System.Collections.Generic.List[object]
    $fixedTotal = 0.0
    $usageTotal = 0.0

    foreach ($c in $meta.cost.components) {
        $qty = if ($null -ne $c.quantityPerMonth) { [double]$c.quantityPerMonth } else { 0 }
        $price = Get-RetailPrice -Component $c -Region $Region -Currency $Currency

        if ($null -eq $price) {
            $rows.Add([pscustomobject]@{
                Component = $c.name
                Billing   = $c.billing
                Unit      = $c.unitOfMeasure
                UnitPrice = 'n/a'
                Qty       = $qty
                Monthly   = 'price not found'
            })
            continue
        }

        $monthly = [math]::Round($price.retailPrice * $qty, 2)
        if ($c.billing -eq 'fixed') { $fixedTotal += $monthly } else { $usageTotal += $monthly }

        $rows.Add([pscustomobject]@{
            Component = $c.name
            Billing   = $c.billing
            Unit      = $price.unitOfMeasure
            UnitPrice = [math]::Round($price.retailPrice, 5)
            Qty       = $qty
            Monthly   = $monthly
        })
    }

    return [pscustomobject]@{
        Name       = $meta.displayName
        Folder     = (Resolve-Path -LiteralPath $Path).Path.Substring((Resolve-Path -LiteralPath $CatalogDir).Path.Length).TrimStart('\','/').Replace('\','/')
        Rows       = $rows
        FixedTotal = [math]::Round($fixedTotal, 2)
        UsageTotal = [math]::Round($usageTotal, 2)
        Tco        = [math]::Round($fixedTotal + $usageTotal, 2)
    }
}

# --- Resolve target use cases ---------------------------------------------------
$targets = @()
if ($All) {
    $targets = Get-ChildItem -Path $CatalogDir -Recurse -Filter 'metadata.json' -File |
        ForEach-Object { $_.DirectoryName } | Sort-Object -Unique
}
elseif ($UseCase) {
    $p = Join-Path $CatalogDir $UseCase
    if (-not (Test-Path (Join-Path $p 'metadata.json'))) { throw "Use case '$UseCase' not found (no metadata.json under $p)." }
    $targets = @($p)
}
else {
    Write-Host "Specify -UseCase <name> or -All. Available use cases:" -ForegroundColor Yellow
    Get-ChildItem -Path $CatalogDir -Recurse -Filter 'metadata.json' -File | ForEach-Object {
        $rel = $_.DirectoryName.Substring((Resolve-Path -LiteralPath $CatalogDir).Path.Length).TrimStart('\','/').Replace('\','/')
        Write-Host "  - $rel"
    }
    return
}

# --- Price and report -----------------------------------------------------------
Write-Host ""
Write-Host "Azure Tech Labs - cost estimate" -ForegroundColor Cyan
Write-Host "Region: $Region   Currency: $Currency   Source: Azure Retail Prices API" -ForegroundColor DarkGray
Write-Host ("Note: usage rows are illustrative, based on assumed monthly quantities in metadata.json.") -ForegroundColor DarkGray

$portfolio = 0.0
foreach ($t in $targets) {
    $result = Get-UseCaseCost -Path $t
    if ($null -eq $result) { continue }

    Write-Host ""
    Write-Host "== $($result.Name) ($($result.Folder)) ==" -ForegroundColor Green
    $result.Rows | Format-Table -AutoSize | Out-String | Write-Host
    Write-Host ("  Committed (fixed): {0} {1,10:N2} / month" -f $Currency, $result.FixedTotal)
    Write-Host ("  Estimated usage  : {0} {1,10:N2} / month" -f $Currency, $result.UsageTotal)
    Write-Host ("  TCO (monthly)    : {0} {1,10:N2} / month" -f $Currency, $result.Tco) -ForegroundColor White
    Write-Host ("  TCO (annual)     : {0} {1,10:N2} / year" -f $Currency, [math]::Round($result.Tco * 12, 2)) -ForegroundColor White
    $portfolio += $result.Tco
}

if ($All) {
    Write-Host ""
    Write-Host ("== Portfolio TCO ==") -ForegroundColor Cyan
    Write-Host ("  {0} {1,10:N2} / month   |   {0} {2,10:N2} / year" -f $Currency, [math]::Round($portfolio, 2), [math]::Round($portfolio * 12, 2)) -ForegroundColor White
}

Write-Host ""
Write-Host "Prices are retail list (PAYG), exclude tax, reservations, savings plans, and free tiers." -ForegroundColor DarkGray
