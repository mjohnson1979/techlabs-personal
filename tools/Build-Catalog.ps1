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
    [string]$OutFile
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$CatalogDir = Join-Path $RepoRoot 'catalog'
if (-not $OutFile) { $OutFile = Join-Path $RepoRoot 'site/catalog.json' }

$metaFiles = Get-ChildItem -Path $CatalogDir -Recurse -Filter 'metadata.json' -File | Sort-Object FullName
$items = New-Object System.Collections.Generic.List[object]

foreach ($mf in $metaFiles) {
    $meta = Get-Content $mf.FullName -Raw | ConvertFrom-Json
    $rel = $mf.DirectoryName.Substring($CatalogDir.Length).TrimStart('\', '/').Replace('\', '/')

    $componentCount = if ($meta.cost -and $meta.cost.components) { @($meta.cost.components).Count } else { 0 }
    $hasProd = Test-Path (Join-Path $mf.DirectoryName 'parameters/prod.bicepparam')

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
