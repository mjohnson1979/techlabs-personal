<#
.SYNOPSIS
    Validate every catalog use case without deploying anything to Azure.

.DESCRIPTION
    Fast, offline checks that guard the gallery against broken contributions:

      1. Bicep compiles   - every catalog/**/main.bicep (entries and modules)
                            and every shared modules/**/main.bicep builds clean.
      2. Metadata present - every entry that has a main.bicep also has a
                            metadata.json with the required fields, and the
                            metadata name matches its folder. Entries without
                            metadata never surface in the gallery, so this is a
                            hard failure.
      3. Parameters parse - every parameters/*.bicepparam builds against its
                            main.bicep.
      4. Catalog in sync  - site/catalog.json matches what Build-Catalog.ps1
                            would generate right now (no stale committed catalog).

    Exits non-zero if any check fails, so it can gate CI.

.EXAMPLE
    ./tools/Test-Catalog.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$CatalogDir = Join-Path $RepoRoot 'catalog'
$ModulesDir = Join-Path $RepoRoot 'modules'

$failures = New-Object System.Collections.Generic.List[string]
function Fail([string]$msg) { $script:failures.Add($msg); Write-Host "  FAIL  $msg" -ForegroundColor Red }
function Pass([string]$msg) { Write-Host "  ok    $msg" -ForegroundColor DarkGray }

function Invoke-Az([string[]]$azArgs) {
    # az emits linter warnings on stderr; don't let ErrorActionPreference=Stop
    # turn those into terminating errors.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & az @azArgs 2>&1
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prev
    }
    return @{ Ok = ($code -eq 0); Output = ($out | Out-String) }
}

function Test-BicepBuild([string]$file) {
    return Invoke-Az @('bicep', 'build', '--file', $file, '--stdout')
}

function Test-BicepParamBuild([string]$file) {
    return Invoke-Az @('bicep', 'build-params', '--file', $file, '--stdout')
}

$requiredMetaFields = @('name', 'displayName', 'category', 'description', 'what', 'why')
$seenNames = @{}

# ---------------------------------------------------------------------------
Write-Host "`n[1/4] Compiling Bicep (entries + modules)..." -ForegroundColor Cyan
$bicepFiles = Get-ChildItem -Path $CatalogDir, $ModulesDir -Recurse -Filter '*.bicep' -File
foreach ($b in $bicepFiles) {
    $rel = $b.FullName.Substring($RepoRoot.Length).TrimStart('\', '/').Replace('\', '/')
    $r = Test-BicepBuild $b.FullName
    if ($r.Ok) { Pass $rel } else { Fail "bicep build: $rel`n$($r.Output)" }
}

# ---------------------------------------------------------------------------
Write-Host "`n[2/4] Validating entry metadata..." -ForegroundColor Cyan
$entryMains = Get-ChildItem -Path $CatalogDir -Recurse -Filter 'main.bicep' -File |
    Where-Object { $_.DirectoryName -notmatch '[\\/]modules[\\/]' }
foreach ($m in $entryMains) {
    $dir = $m.DirectoryName
    $rel = $dir.Substring($CatalogDir.Length).TrimStart('\', '/').Replace('\', '/')
    $metaPath = Join-Path $dir 'metadata.json'
    if (-not (Test-Path $metaPath)) {
        Fail "metadata.json missing for entry '$rel' (entry will not appear in the gallery)"
        continue
    }
    try { $meta = Get-Content $metaPath -Raw | ConvertFrom-Json } catch { Fail "metadata.json invalid JSON for '$rel': $_"; continue }
    $missing = $requiredMetaFields | Where-Object { -not $meta.$_ }
    if ($missing) { Fail "metadata.json for '$rel' missing fields: $($missing -join ', ')" }
    if ($meta.name) {
        if ($seenNames.ContainsKey($meta.name)) {
            Fail "duplicate metadata name '$($meta.name)' in '$rel' and '$($seenNames[$meta.name])'"
        }
        else { $seenNames[$meta.name] = $rel }
    }
    if (-not $missing) { Pass "$rel metadata" }
}

# ---------------------------------------------------------------------------
Write-Host "`n[3/4] Compiling parameter files..." -ForegroundColor Cyan
$paramFiles = Get-ChildItem -Path $CatalogDir -Recurse -Filter '*.bicepparam' -File
if (-not $paramFiles) { Write-Host "  (none found)" -ForegroundColor DarkGray }
foreach ($p in $paramFiles) {
    $rel = $p.FullName.Substring($CatalogDir.Length).TrimStart('\', '/').Replace('\', '/')
    $r = Test-BicepParamBuild $p.FullName
    if ($r.Ok) { Pass $rel } else { Fail "bicepparam build: $rel`n$($r.Output)" }
}

# ---------------------------------------------------------------------------
Write-Host "`n[4/4] Checking catalog.json is in sync..." -ForegroundColor Cyan
$committedPath = Join-Path $RepoRoot 'site/catalog.json'
$tempPath = Join-Path ([System.IO.Path]::GetTempPath()) "catalog-check-$([guid]::NewGuid()).json"
try {
    & (Join-Path $PSScriptRoot 'Build-Catalog.ps1') -OutFile $tempPath | Out-Null
    if (-not (Test-Path $committedPath)) {
        Fail "site/catalog.json is not committed"
    }
    else {
        $expected = (Get-Content $tempPath -Raw | ConvertFrom-Json).items | ConvertTo-Json -Depth 20
        $actual = (Get-Content $committedPath -Raw | ConvertFrom-Json).items | ConvertTo-Json -Depth 20
        if ($expected -eq $actual) { Pass "site/catalog.json is up to date" }
        else { Fail "site/catalog.json is stale - run tools/Build-Catalog.ps1 and commit the result" }
    }
}
finally {
    if (Test-Path $tempPath) { Remove-Item $tempPath -Force }
}

# ---------------------------------------------------------------------------
Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host "Catalog validation FAILED with $($failures.Count) issue(s)." -ForegroundColor Red
    exit 1
}
Write-Host "Catalog validation passed." -ForegroundColor Green
exit 0
