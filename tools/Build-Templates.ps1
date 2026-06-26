<#
.SYNOPSIS
    Compile every catalog use case's Bicep into ARM JSON for the gallery backend.

.DESCRIPTION
    Option B: the gallery backend deploys use cases directly with its App Service
    managed identity, so it needs ready-to-submit ARM templates at runtime (it has
    no Bicep CLI). For each catalog/<use_case>/parameters/<env>.bicepparam this
    runs `bicep build-params` and writes
    site/server/_templates/<use_case>/<env>.json with the shape
        { "template": <arm template>, "parameters": <arm parameters> }

    Run this locally before testing deploys, and in CI before publishing the site
    (see .github/workflows/deploy-gallery.yml). Requires the standalone `bicep`
    CLI or the Azure CLI (`az bicep`) on PATH.

.EXAMPLE
    ./tools/Build-Templates.ps1
#>
[CmdletBinding()]
param(
    [string]$CatalogDir,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $CatalogDir) { $CatalogDir = Join-Path $RepoRoot 'catalog' }
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot 'site/server/_templates' }

# Resolve how to invoke Bicep: prefer the standalone CLI, fall back to az bicep.
$bicepMode = $null
if (Get-Command bicep -ErrorAction SilentlyContinue) { $bicepMode = 'bicep' }
elseif (Get-Command az -ErrorAction SilentlyContinue) { $bicepMode = 'az' }
else {
    throw "Bicep is required. Install the standalone 'bicep' CLI or the Azure CLI."
}

function Get-CompiledParams {
    param([string]$ParamFile)
    if ($bicepMode -eq 'bicep') {
        $raw = & bicep build-params $ParamFile --stdout 2>$null | Out-String
    }
    else {
        $raw = & az bicep build-params --file $ParamFile --stdout 2>$null | Out-String
    }
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
        throw "bicep build-params failed for $ParamFile"
    }
    return $raw | ConvertFrom-Json
}

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$paramFiles = Get-ChildItem -Path $CatalogDir -Recurse -Filter '*.bicepparam' -File |
    Sort-Object FullName
$count = 0

foreach ($pf in $paramFiles) {
    $envName = [IO.Path]::GetFileNameWithoutExtension($pf.Name)   # dev / prod
    $useCaseDir = Split-Path -Parent $pf.DirectoryName            # .../catalog/<use_case>
    $rel = [IO.Path]::GetRelativePath($CatalogDir, $useCaseDir).Replace('\', '/')

    $doc = Get-CompiledParams -ParamFile $pf.FullName
    if (-not $doc.templateJson -or -not $doc.parametersJson) {
        throw "Unexpected bicep output for $($pf.FullName) (missing templateJson/parametersJson)"
    }
    $template = $doc.templateJson | ConvertFrom-Json
    $parameters = ($doc.parametersJson | ConvertFrom-Json).parameters

    $out = [ordered]@{ template = $template; parameters = $parameters }
    $destDir = Join-Path $OutDir $rel
    New-Item -ItemType Directory -Force -Path $destDir | Out-Null
    $out | ConvertTo-Json -Depth 100 |
        Set-Content -Path (Join-Path $destDir "$envName.json") -Encoding UTF8

    Write-Host "  built $rel/$envName.json"
    $count++
}

Write-Host "Built $count compiled template(s) into $OutDir"
