<#
.SYNOPSIS
Runs a throwaway local SQL Server 2022 container and deploys WidestWarehouse into it for testing.

.PARAMETER Database
Database name to create in the throwaway SQL Server container.

.PARAMETER Port
Host TCP port mapped to container port 1433.

.PARAMETER ContainerName
Name for the temporary Docker container.

.PARAMETER SaPassword
SA password as a SecureString or plain string. If omitted, DW_TEST_SQL_PASSWORD is used, then a throwaway default.

.PARAMETER KeepContainer
Leaves the test container running after verification instead of removing it.

.PARAMETER SkipSeed
Passes -SkipSeed to deploy.ps1.

.EXAMPLE
.\scripts\test_local_sqlserver.ps1 -Port 11433

.EXAMPLE
.\scripts\test_local_sqlserver.ps1 -KeepContainer
#>
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$Database = 'WidestWarehouse',

    [int]$Port = 11433,

    [ValidateNotNullOrEmpty()]
    [string]$ContainerName = ("ww-sqlserver-test-{0}" -f ([guid]::NewGuid().ToString('N').Substring(0, 8))),

    [object]$SaPassword,

    [switch]$KeepContainer,

    [switch]$SkipSeed
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

# This script is for local verification only. The shipped loader\docker-compose.yml
# intentionally does not include SQL Server; users connect deployments to their own SQL Server instance.

function ConvertTo-PlainTextPassword {
    param([object]$Value)

    if ($null -eq $Value) {
        if (-not [string]::IsNullOrEmpty($env:DW_TEST_SQL_PASSWORD)) {
            return $env:DW_TEST_SQL_PASSWORD
        }
        return 'Ww_Test_Strong_Passw0rd!'
    }

    if ($Value -is [System.Security.SecureString]) {
        $bstr = [IntPtr]::Zero
        try {
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
            return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        }
        finally {
            if ($bstr -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
        }
    }

    return [string]$Value
}

function Assert-Command {
    param([string]$Name, [string]$InstallHint)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "$Name was not found on PATH. $InstallHint"
    }
    return $command.Source
}

function Invoke-SqlcmdChecked {
    param(
        [string]$Description,
        [string]$TargetDatabase,
        [string]$InputFile,
        [string]$Query
    )

    Write-Host "==> $Description"
    $oldPassword = $env:SQLCMDPASSWORD
    $env:SQLCMDPASSWORD = $script:PlainPassword
    try {
        $args = @('-S', "localhost,$Port", '-U', 'sa', '-b', '-V', '16', '-C')
        if (-not [string]::IsNullOrEmpty($TargetDatabase)) {
            $args += @('-d', $TargetDatabase)
        }
        if (-not [string]::IsNullOrEmpty($InputFile)) {
            $args += @('-i', $InputFile)
        }
        if (-not [string]::IsNullOrEmpty($Query)) {
            $args += @('-Q', $Query)
        }
        & $script:SqlcmdPath @args
        if ($LASTEXITCODE -ne 0) {
            throw "sqlcmd failed during '$Description' with exit code $LASTEXITCODE."
        }
    }
    finally {
        if ($null -eq $oldPassword) {
            Remove-Item Env:\SQLCMDPASSWORD -ErrorAction SilentlyContinue
        }
        else {
            $env:SQLCMDPASSWORD = $oldPassword
        }
    }
}

try {
if ($Port -lt 1 -or $Port -gt 65535) {
    throw 'Port must be between 1 and 65535.'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$deployScript = Join-Path $PSScriptRoot 'deploy.ps1'
$countScript = Join-Path $PSScriptRoot 'count_tables.sql'
$verifyScript = Join-Path $PSScriptRoot 'verify_schema.sql'
$dockerPath = Assert-Command -Name 'docker' -InstallHint 'Install Docker Desktop and ensure Docker is running.'
$script:SqlcmdPath = Assert-Command -Name 'sqlcmd' -InstallHint 'Install Microsoft sqlcmd before running local SQL Server tests.'
$script:PlainPassword = ConvertTo-PlainTextPassword -Value $SaPassword
$powershellExe = if ($PSVersionTable.PSEdition -eq 'Core') { (Get-Command pwsh).Source } else { (Get-Command powershell).Source }
$containerStarted = $false

try {
    Write-Host '==> Starting throwaway SQL Server 2022 test container'
    $volume = $repoRoot + ':/ww:ro'
    & $dockerPath run --name $ContainerName `
        -e 'ACCEPT_EULA=Y' `
        -e "MSSQL_SA_PASSWORD=$script:PlainPassword" `
        -p "$Port`:1433" `
        -v $volume `
        -d 'mcr.microsoft.com/mssql/server:2022-latest' | Out-Null

    if ($LASTEXITCODE -ne 0) {
        throw "docker run failed with exit code $LASTEXITCODE."
    }
    $containerStarted = $true

    Write-Host '==> Waiting for SQL Server to accept connections'
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 5
        try {
            Invoke-SqlcmdChecked -Description 'Container connectivity check' -TargetDatabase 'master' -Query 'SET NOCOUNT ON; SELECT 1;'
            $ready = $true
        }
        catch {
            $ready = $false
            if ((Get-Date) -ge $deadline) {
                throw "SQL Server container did not become ready within 3 minutes. Last error: $($_.Exception.Message)"
            }
        }
    } until ($ready)

    $oldSeedRoot = $env:DW_SEED_SERVER_ROOT
    $oldDeployPassword = $env:DW_PASSWORD
    $env:DW_SEED_SERVER_ROOT = '/ww/seed'
    $env:DW_PASSWORD = $script:PlainPassword
    try {
        $deployArgs = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', $deployScript,
            '-Server', "localhost,$Port",
            '-Database', $Database,
            '-User', 'sa',
            '-TrustServerCertificate',
            '-Force',
            '-Confirm:$false'
        )
        if ($SkipSeed) {
            $deployArgs += '-SkipSeed'
        }
        & $powershellExe @deployArgs
        if ($LASTEXITCODE -ne 0) {
            throw "deploy.ps1 failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        if ($null -eq $oldSeedRoot) {
            Remove-Item Env:\DW_SEED_SERVER_ROOT -ErrorAction SilentlyContinue
        }
        else {
            $env:DW_SEED_SERVER_ROOT = $oldSeedRoot
        }
        if ($null -eq $oldDeployPassword) {
            Remove-Item Env:\DW_PASSWORD -ErrorAction SilentlyContinue
        }
        else {
            $env:DW_PASSWORD = $oldDeployPassword
        }
    }

    Invoke-SqlcmdChecked -Description 'Table-count verification' -TargetDatabase $Database -InputFile $countScript
    Invoke-SqlcmdChecked -Description 'Structural schema verification' -TargetDatabase $Database -InputFile $verifyScript
    Write-Host 'Local SQL Server deployment test passed.'
}
finally {
    if ($containerStarted -and -not $KeepContainer) {
        Write-Host '==> Removing throwaway SQL Server test container'
        & $dockerPath rm -f $ContainerName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Failed to remove container '$ContainerName'. Remove it manually with: docker rm -f $ContainerName"
        }
    }
    elseif ($containerStarted) {
        Write-Host "Keeping container '$ContainerName' on localhost,$Port."
    }
}
}
catch {
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
