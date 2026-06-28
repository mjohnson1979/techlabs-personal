<#
.SYNOPSIS
    Builds site/updates.json - a static snapshot of Azure service lifecycle
    changes (retirements, previews, and recent GA) relevant to the catalog.

.DESCRIPTION
    Pulls the public Microsoft Release Communications (MRC) feed that powers
    https://azure.microsoft.com/updates and projects each update onto the same
    "icon key" vocabulary the catalog already uses for services
    (see site/assets/azure-icons.js). The gallery then surfaces, per use case,
    any upcoming retirements / new previews / recent GA for the services that
    use case actually deploys.

    The feed is a public, anonymous OData endpoint, so this tool needs no
    Azure sign-in. It is best-effort: a network failure leaves any existing
    updates.json untouched and the gallery degrades gracefully.

.PARAMETER OutFile
    Where to write the snapshot. Default: site/updates.json next to the repo.

.PARAMETER MaxPages
    Safety cap on feed pages (100 items each) walked per run. The feed is
    sorted newest-modified-first, so recent lifecycle changes appear early.

.PARAMETER RecentMonths
    How far back a Preview / GA item may be (by its ring date) and still be
    reported. Retirements are always reported regardless of age (they are
    future-dated). Default: 12 months.

.EXAMPLE
    pwsh ./tools/Build-Updates.ps1
#>
[CmdletBinding()]
param(
    [string]$OutFile,
    [int]$MaxPages = 40,
    [int]$RecentMonths = 12
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutFile) { $OutFile = Join-Path $RepoRoot 'site/updates.json' }

$FeedRoot = 'https://www.microsoft.com/releasecommunications/api/v2/azure'

# The feed expresses an availability ring's month as either a number (10) or a
# month name ("October"). Normalize both to a 1-12 integer, or 0 if unknown.
$MonthNames = @{}
for ($i = 1; $i -le 12; $i++) {
    $name = (Get-Culture).DateTimeFormat.GetMonthName($i).ToLowerInvariant()
    $MonthNames[$name] = $i
    $MonthNames[$name.Substring(0, 3)] = $i
}
function ConvertTo-MonthNumber {
    param($Value)
    if ($null -eq $Value) { return 0 }
    $s = "$Value".Trim()
    $n = 0
    if ([int]::TryParse($s, [ref]$n)) { return $n }
    $key = $s.ToLowerInvariant()
    if ($MonthNames.ContainsKey($key)) { return $MonthNames[$key] }
    return 0
}

# Map MRC "products" strings onto the catalog icon-key vocabulary. Keys here
# match site/assets/azure-icons.js so the frontend can join on a use case's
# cost-meter serviceNames (via AzureIcons.keyForService) with no extra mapping.
# Matching is case-insensitive substring: the first alias found in a product
# name wins, so order longer/more-specific aliases first within a key.
$ProductAliases = [ordered]@{
    openai         = @('azure openai', 'azure ai services', 'cognitive services', 'azure ai foundry', 'azure machine learning')
    search         = @('azure ai search', 'cognitive search', 'azure search')
    cosmos         = @('cosmos db')
    sql            = @('sql database', 'sql managed instance', 'azure sql', 'sql server on azure')
    appservice     = @('app service', 'web app')
    containerapps  = @('container apps')
    aks            = @('kubernetes service', 'aks', 'red hat openshift')
    acr            = @('container registry')
    functions      = @('azure functions', 'function app')
    logicapps      = @('logic apps')
    apim           = @('api management')
    appgateway     = @('application gateway')
    firewall       = @('azure firewall', 'web application firewall')
    bastion        = @('azure bastion')
    frontdoor      = @('front door')
    expressroute   = @('expressroute')
    vnet           = @('virtual network')
    vm             = @('virtual machines', 'virtual machine scale sets')
    avd            = @('azure virtual desktop')
    avs            = @('vmware solution')
    storage        = @('blob storage', 'azure files', 'storage accounts', 'azure netapp files', 'archive storage', 'azure storage')
    keyvault       = @('key vault')
    servicebus     = @('service bus')
    eventhubs      = @('event hubs')
    streamanalytics= @('stream analytics')
    datafactory    = @('data factory')
    databricks     = @('databricks')
    monitor        = @('azure monitor', 'application insights')
    loganalytics   = @('log analytics')
}

function Resolve-ServiceKeys {
    param([string[]]$Products)
    $keys = New-Object System.Collections.Generic.HashSet[string]
    foreach ($p in $Products) {
        if (-not $p) { continue }
        $lp = $p.ToLowerInvariant()
        foreach ($key in $ProductAliases.Keys) {
            foreach ($alias in $ProductAliases[$key]) {
                if ($lp.Contains($alias)) { [void]$keys.Add($key); break }
            }
        }
    }
    return @($keys)
}

# Classify one feed item into retirement / preview / launched, returning the
# ring date (YYYY-MM) where known. Retirements use the future-dated Retirement
# availability ring; preview/GA use the matching dates.
function Get-Lifecycle {
    param($Item)

    $tags = @($Item.tags | ForEach-Object { "$_".ToLowerInvariant() })
    $isRetire = $false
    $retireYM = $null
    foreach ($a in @($Item.availabilities)) {
        if ($a -and "$($a.ring)".ToLowerInvariant() -eq 'retirement') {
            $isRetire = $true
            $m = ConvertTo-MonthNumber $a.month
            if ($a.year -and $m -gt 0) { $retireYM = '{0:0000}-{1:00}' -f [int]$a.year, $m }
        }
    }
    if ($tags -contains 'retirements') { $isRetire = $true }

    if ($isRetire) {
        return [pscustomobject]@{ type = 'retirement'; date = $retireYM }
    }

    $status = "$($Item.status)".ToLowerInvariant()
    if ($status -eq 'in preview' -or $Item.previewAvailabilityDate) {
        $ym = $null
        if ($Item.previewAvailabilityDate) { $ym = ([datetime]$Item.previewAvailabilityDate).ToString('yyyy-MM') }
        return [pscustomobject]@{ type = 'preview'; date = $ym }
    }
    if ($status -eq 'launched' -or $Item.generalAvailabilityDate) {
        $ym = $null
        if ($Item.generalAvailabilityDate) { $ym = ([datetime]$Item.generalAvailabilityDate).ToString('yyyy-MM') }
        return [pscustomobject]@{ type = 'launched'; date = $ym }
    }
    return $null
}

Write-Host "Fetching Azure Updates feed (max $MaxPages pages)..." -ForegroundColor Cyan

$all = New-Object System.Collections.Generic.List[object]
$url = $FeedRoot
$page = 0
while ($url -and $page -lt $MaxPages) {
    $page++
    try {
        $resp = Invoke-RestMethod -Uri $url -Method Get -Headers @{ Accept = 'application/json' } -TimeoutSec 60
    } catch {
        Write-Warning "Feed request failed on page $page : $($_.Exception.Message)"
        break
    }
    if ($resp.value) { foreach ($v in $resp.value) { $all.Add($v) } }
    $next = $resp.'@odata.nextLink'
    $url = if ($next) { $next } else { $null }
}

Write-Host ("  collected {0} updates across {1} page(s)" -f $all.Count, $page) -ForegroundColor DarkGray

if ($all.Count -eq 0) {
    Write-Warning "No updates collected; leaving any existing $OutFile untouched."
    exit 0
}

$cutoff = (Get-Date).AddMonths(-$RecentMonths)

# services: icon-key -> { retirements:[], previews:[], launched:[] }
$services = @{}
function Add-Entry {
    param([string]$Key, [string]$Bucket, $Entry)
    if (-not $services.ContainsKey($Key)) {
        $services[$Key] = [ordered]@{ retirements = (New-Object System.Collections.Generic.List[object]); previews = (New-Object System.Collections.Generic.List[object]); launched = (New-Object System.Collections.Generic.List[object]) }
    }
    $services[$Key][$Bucket].Add($Entry)
}

$retireGlobal = New-Object System.Collections.Generic.List[object]
$seen = New-Object System.Collections.Generic.HashSet[string]

foreach ($item in $all) {
    $life = Get-Lifecycle -Item $item
    if (-not $life) { continue }

    # For non-retirements, drop anything older than the recent window.
    if ($life.type -ne 'retirement' -and $life.date) {
        try { if ([datetime]("$($life.date)-01") -lt $cutoff) { continue } } catch { }
    }

    $keys = @(Resolve-ServiceKeys -Products (@($item.products)))
    if (-not $keys -or $keys.Count -eq 0) { continue }

    $entry = [ordered]@{
        id    = "$($item.id)"
        title = "$($item.title)"
        url   = "https://azure.microsoft.com/updates?id=$($item.id)"
        date  = $life.date
    }

    $bucket = switch ($life.type) { 'retirement' { 'retirements' } 'preview' { 'previews' } default { 'launched' } }
    foreach ($k in $keys) { Add-Entry -Key $k -Bucket $bucket -Entry $entry }

    if ($life.type -eq 'retirement' -and $seen.Add("$($item.id)")) {
        $retireGlobal.Add(([ordered]@{ id = $entry.id; title = $entry.title; url = $entry.url; date = $entry.date; services = $keys }))
    }
}

# Trim and date-sort each bucket so the UI shows the most relevant few.
function Sort-Bucket {
    param($List, [switch]$Ascending)
    $sorted = @($List | Sort-Object -Property @{ Expression = { if ($_.date) { $_.date } else { '0000-00' } } } -Descending:(-not $Ascending))
    return $sorted
}

# Drop duplicate titles (the feed often re-issues the same retirement notice)
# and, for retirements, anything already in the past (no longer actionable).
$nowYM = (Get-Date).ToString('yyyy-MM')
function Select-Bucket {
    param($List, [string]$Bucket)
    $out = New-Object System.Collections.Generic.List[object]
    $titles = New-Object System.Collections.Generic.HashSet[string]
    foreach ($e in $List) {
        if ($Bucket -eq 'retirements' -and $e.date -and $e.date -lt $nowYM) { continue }
        $tkey = "$($e.title)".Trim().ToLowerInvariant()
        if (-not $titles.Add($tkey)) { continue }
        $out.Add($e)
    }
    return $out.ToArray()
}

$svcOut = [ordered]@{}
foreach ($key in ($services.Keys | Sort-Object)) {
    $s = $services[$key]
    $svcOut[$key] = [ordered]@{
        retirements = @(Select-Bucket -List (Sort-Bucket -List $s.retirements -Ascending) -Bucket 'retirements' | Select-Object -First 6)
        previews    = @(Select-Bucket -List (Sort-Bucket -List $s.previews) -Bucket 'previews' | Select-Object -First 6)
        launched    = @(Select-Bucket -List (Sort-Bucket -List $s.launched) -Bucket 'launched' | Select-Object -First 6)
    }
}

$snapshot = [ordered]@{
    generated      = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    source         = 'https://azure.microsoft.com/updates'
    pagesScanned   = $page
    updatesScanned = $all.Count
    retirements    = @(Select-Bucket -List (@($retireGlobal | Sort-Object -Property @{ Expression = { if ($_.date) { $_.date } else { '9999-99' } } })) -Bucket 'retirements' | Select-Object -First 60)
    services       = $svcOut
}

$dir = Split-Path -Parent $OutFile
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
$snapshot | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding UTF8

$svcCount = @($svcOut.Keys).Count
Write-Host ("Wrote {0} service buckets ({1} retirements) to {2}" -f $svcCount, $retireGlobal.Count, $OutFile) -ForegroundColor Green
