<#
.SYNOPSIS
    Cut a semantic-version release of the loader container.

.DESCRIPTION
    Bumps loader/app/version.py, dates the Unreleased section of CHANGELOG.md, runs the
    test suite, commits, and creates an annotated v* tag. Pushing that tag is what makes
    the publish workflow build and release the image, so nothing is published until you
    pass -Push (or push the tag yourself).

.EXAMPLE
    .\scripts\release.ps1 -Bump minor -Push

.EXAMPLE
    .\scripts\release.ps1 -Version 2.0.0-rc.1
#>
[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'High', DefaultParameterSetName = 'Bump')]
param(
    [Parameter(ParameterSetName = 'Bump', Mandatory)]
    [ValidateSet('major', 'minor', 'patch')]
    [string]$Bump,

    [Parameter(ParameterSetName = 'Explicit', Mandatory)]
    [string]$Version,

    [switch]$Push,

    [switch]$SkipTests,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments, [string]$ErrorMessage)
    $output = & $Exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$ErrorMessage`n$($output -join [Environment]::NewLine)"
    }
    return $output
}

function Invoke-ReleaseCli {
    param([string[]]$Arguments)
    Push-Location $repoRoot
    try {
        return (Invoke-Checked -Exe 'python' -Arguments (@('-m', 'tools.release.cli') + $Arguments) `
                -ErrorMessage 'Release CLI failed.') -join [Environment]::NewLine
    } finally {
        Pop-Location
    }
}

Push-Location $repoRoot
try {
    Write-Host '==> Preflight' -ForegroundColor Cyan

    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne 'main' -and -not $Force) {
        throw "Releases are cut from main; HEAD is on '$branch'. Use -Force to override."
    }

    $dirty = & git status --porcelain
    if ($dirty -and -not $Force) {
        throw "The working tree has uncommitted changes. Commit or stash them first, or use -Force.`n$($dirty -join [Environment]::NewLine)"
    }

    $current = Invoke-ReleaseCli @('current')
    if ($PSCmdlet.ParameterSetName -eq 'Bump') {
        $target = Invoke-ReleaseCli @('next', '--bump', $Bump)
    } else {
        $target = $Version.TrimStart('v')
    }

    $tag = "v$target"
    $existingTag = & git tag --list $tag
    if ($existingTag) {
        throw "Tag $tag already exists. Releases are immutable; pick a new version."
    }

    Write-Host "    current version : $current"
    Write-Host "    release version : $target"
    Write-Host "    tag             : $tag"

    if (-not $PSCmdlet.ShouldProcess($tag, 'Create release commit and tag')) {
        return
    }

    if (-not $SkipTests) {
        Write-Host '==> Tests' -ForegroundColor Cyan
        Invoke-Checked -Exe 'python' -Arguments @('-m', 'pytest', 'tools/tests', 'loader/tests', '-q') `
            -ErrorMessage 'Tests failed; release aborted.' | Select-Object -Last 2 | Write-Host
    }

    Write-Host '==> Stamp version and changelog' -ForegroundColor Cyan
    $alreadyStamped = $false
    if ($current -eq $target) {
        # Either the very first release, whose changelog section was written by hand, or a
        # re-run after a partial release. Re-stamping would fail on an empty Unreleased
        # section, so verify the existing state instead of rewriting it.
        try {
            Invoke-ReleaseCli @('notes', '--version', $target) | Out-Null
            $alreadyStamped = $true
            Write-Host "    $target is already stamped and documented; tagging only."
        } catch {
            throw "version.py is already at $target but CHANGELOG.md has no notes for it. Add them, or pick a new version."
        }
    }
    if (-not $alreadyStamped) {
        Invoke-ReleaseCli @('set', $target) | Write-Host
    }

    # Prove the tag we are about to create will survive the workflow's guard.
    Invoke-ReleaseCli @('check', '--tag', $tag) | Write-Host

    Write-Host '==> Commit and tag' -ForegroundColor Cyan
    if (-not $alreadyStamped) {
        Invoke-Checked -Exe 'git' -Arguments @('add', 'loader/app/version.py', 'CHANGELOG.md') `
            -ErrorMessage 'git add failed.' | Out-Null
        Invoke-Checked -Exe 'git' -Arguments @('commit', '-m', "Release $tag") `
            -ErrorMessage 'git commit failed.' | Out-Null
    }
    Invoke-Checked -Exe 'git' -Arguments @('tag', '-a', $tag, '-m', "Release $tag") `
        -ErrorMessage 'git tag failed.' | Out-Null

    if ($Push) {
        Write-Host '==> Push' -ForegroundColor Cyan
        Invoke-Checked -Exe 'git' -Arguments @('push', 'origin', 'HEAD') -ErrorMessage 'git push failed.' | Out-Null
        Invoke-Checked -Exe 'git' -Arguments @('push', 'origin', $tag) -ErrorMessage 'git push --tags failed.' | Out-Null
        Write-Host "Pushed $tag. The publish workflow will build the image and create the GitHub Release." -ForegroundColor Green
    } else {
        Write-Host "Created $tag locally. Publish it with:" -ForegroundColor Yellow
        Write-Host "    git push origin HEAD && git push origin $tag"
    }
} finally {
    Pop-Location
}
