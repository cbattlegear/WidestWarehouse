<#
.SYNOPSIS
Regenerates the sql\ folder from YAML model metadata.

.PARAMETER SkipInstall
Skips installing tools\requirements.txt before running the generator.

.EXAMPLE
.\scripts\rebuild_model.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

try {
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$requirements = Join-Path $repoRoot 'tools\requirements.txt'
$generatorCli = Join-Path $repoRoot 'tools\generator\cli.py'

Write-Host 'Reminder: sql\ is generated output. Do not hand-edit generated SQL files.'

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    throw 'python was not found on PATH. Install Python 3, then rerun scripts\rebuild_model.ps1.'
}

if (-not (Test-Path $requirements -PathType Leaf)) {
    throw "Cannot find '$requirements'. The tools\ tree may still be incomplete; rerun this script after generator files are available."
}

if (-not $SkipInstall) {
    Write-Host '==> Installing generator requirements'
    & $python.Source -m pip install --disable-pip-version-check -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed with exit code $LASTEXITCODE."
    }
}

if (-not (Test-Path $generatorCli -PathType Leaf)) {
    throw "Cannot find '$generatorCli'. The tools\ tree may still be incomplete; rerun this script after generator files are available."
}

Push-Location $repoRoot
try {
    Write-Host '==> Emitting generated SQL to sql\'
    & $python.Source -m tools.generator.cli emit --out sql
    if ($LASTEXITCODE -ne 0) {
        throw "Generator emit failed with exit code $LASTEXITCODE."
    }

    Write-Host '==> Generated model table counts'
    & $python.Source -m tools.generator.cli stats
    if ($LASTEXITCODE -ne 0) {
        throw "Generator stats failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
}
catch {
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
