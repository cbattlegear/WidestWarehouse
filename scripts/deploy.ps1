<#
.SYNOPSIS
Deploys the generated WidestWarehouse SQL Server schema and seed data.

.PARAMETER Server
SQL Server name or network address, for example localhost,1433.

.PARAMETER Database
Target database name. Defaults to WidestWarehouse.

.PARAMETER User
SQL login name. Omit when using -IntegratedSecurity.

.PARAMETER Password
SQL login password as a SecureString or plain string. If omitted, DW_PASSWORD is used when set.

.PARAMETER TrustServerCertificate
Passes sqlcmd's trust-server-certificate option for development/test instances.

.PARAMETER IntegratedSecurity
Uses the current Windows identity instead of SQL authentication.

.PARAMETER SkipSeed
Skips seed loading (sql\95_seed and the seed\ CSV files).

.PARAMETER SeedRoot
Path to the seed\ folder *as the SQL Server itself sees it*. BULK INSERT reads files
server-side, so a remote or containerised instance needs a path it can reach. Defaults
to DW_SEED_SERVER_ROOT, then to the repository's seed\ folder.

.PARAMETER Force
Drops and recreates the target database. Requires confirmation unless -Confirm:$false is supplied.

.EXAMPLE
.\scripts\deploy.ps1 -Server localhost,1433 -User sa -Password (Read-Host -AsSecureString) -TrustServerCertificate

.EXAMPLE
.\scripts\deploy.ps1 -Server . -IntegratedSecurity -Force -Confirm:$false
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Server,

    [ValidateNotNullOrEmpty()]
    [string]$Database = 'WidestWarehouse',

    [string]$User,

    [object]$Password,

    [switch]$TrustServerCertificate,

    [switch]$IntegratedSecurity,

    [switch]$SkipSeed,

    [string]$SeedRoot,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Resolve-RepositoryRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

function ConvertTo-PlainTextPassword {
    param([object]$Value)

    if ($null -eq $Value) {
        if (-not [string]::IsNullOrEmpty($env:DW_PASSWORD)) {
            return $env:DW_PASSWORD
        }
        return $null
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

function Assert-Sqlcmd {
    $command = Get-Command sqlcmd -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw @'
sqlcmd was not found on PATH.

Install SQL Server command-line tools, then reopen PowerShell:
  winget install Microsoft.Sqlcmd
or install the Microsoft SQL Server Command Line Utilities from:
  https://learn.microsoft.com/sql/tools/sqlcmd/sqlcmd-utility
'@
    }
    return $command.Source
}

function New-SqlcmdArguments {
    param(
        [string]$TargetDatabase,
        [string]$InputFile,
        [string]$Query,
        [hashtable]$Variables
    )

    $args = @('-S', $Server, '-b', '-V', '16')
    if (-not [string]::IsNullOrEmpty($TargetDatabase)) {
        $args += @('-d', $TargetDatabase)
    }
    if ($IntegratedSecurity) {
        $args += '-E'
    }
    else {
        $args += @('-U', $User)
    }
    if ($TrustServerCertificate) {
        $args += '-C'
    }
    if ($Variables) {
        foreach ($key in $Variables.Keys) {
            # The value must stay quoted: sqlcmd treats a leading '/' as an option prefix
            # and rejects unquoted POSIX paths.
            $args += @('-v', ('{0}="{1}"' -f $key, $Variables[$key]))
        }
    }
    if (-not [string]::IsNullOrEmpty($InputFile)) {
        $args += @('-i', $InputFile)
    }
    if (-not [string]::IsNullOrEmpty($Query)) {
        $args += @('-Q', $Query)
    }
    return $args
}

function Invoke-SqlcmdChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description,
        [string]$TargetDatabase,
        [string]$InputFile,
        [string]$Query,
        [string]$WorkingDirectory,
        [hashtable]$Variables
    )

    Write-Host "==> $Description"
    $phaseStart = Get-Date
    $oldPassword = $env:SQLCMDPASSWORD
    if (-not $IntegratedSecurity) {
        $env:SQLCMDPASSWORD = $script:PlainPassword
    }

    $previousLocation = (Get-Location).Path
    try {
        if (-not [string]::IsNullOrEmpty($WorkingDirectory)) {
            Set-Location $WorkingDirectory
        }
        $arguments = New-SqlcmdArguments -TargetDatabase $TargetDatabase -InputFile $InputFile -Query $Query -Variables $Variables
        & $script:SqlcmdPath @arguments
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "sqlcmd failed during '$Description' with exit code $exitCode."
        }
    }
    finally {
        Set-Location $previousLocation
        if (-not $IntegratedSecurity) {
            if ($null -eq $oldPassword) {
                Remove-Item Env:\SQLCMDPASSWORD -ErrorAction SilentlyContinue
            }
            else {
                $env:SQLCMDPASSWORD = $oldPassword
            }
        }
    }

    $duration = New-TimeSpan -Start $phaseStart -End (Get-Date)
    $script:PhaseSummaries += [pscustomobject]@{ Phase = $Description; Duration = $duration }
    Write-Host ("    completed in {0:n1}s" -f $duration.TotalSeconds)
}

function ConvertTo-SqlLiteral {
    param([string]$Value)
    return "N'" + $Value.Replace("'", "''") + "'"
}

function Resolve-SeedRoot {
    <#
    BULK INSERT reads the CSV files from the *server's* filesystem, so a remote or
    containerised SQL Server needs DW_SEED_SERVER_ROOT (or -SeedRoot) pointing at
    the path where seed\ is visible to the engine.
    #>
    if (-not [string]::IsNullOrEmpty($SeedRoot)) {
        return $SeedRoot.TrimEnd('\', '/')
    }
    if (-not [string]::IsNullOrEmpty($env:DW_SEED_SERVER_ROOT)) {
        return $env:DW_SEED_SERVER_ROOT.TrimEnd('\', '/')
    }
    return (Join-Path $script:RepoRoot 'seed').TrimEnd('\', '/')
}

try {
$script:RepoRoot = Resolve-RepositoryRoot
$sqlRoot = Join-Path $script:RepoRoot 'sql'
$buildFile = Join-Path $sqlRoot 'build_all.sql'
$countScript = Join-Path $PSScriptRoot 'count_tables.sql'
$script:PhaseSummaries = @()
$script:SqlcmdPath = Assert-Sqlcmd

if (-not $IntegratedSecurity) {
    if ([string]::IsNullOrEmpty($User)) {
        throw 'Specify -User for SQL authentication or use -IntegratedSecurity.'
    }
    $script:PlainPassword = ConvertTo-PlainTextPassword -Value $Password
    if ([string]::IsNullOrEmpty($script:PlainPassword)) {
        throw 'No password was supplied. Pass -Password securely or set the DW_PASSWORD environment variable.'
    }
}

if (-not (Test-Path $sqlRoot -PathType Container) -or -not (Test-Path $buildFile -PathType Leaf)) {
    throw "Generated SQL was not found at '$buildFile'. Run scripts\rebuild_model.ps1 first; sql\ is generated output and must not be hand-edited."
}

if (-not (Test-Path $countScript -PathType Leaf)) {
    throw "Verification script was not found at '$countScript'."
}

Invoke-SqlcmdChecked -Description 'Connectivity check' -TargetDatabase 'master' -Query 'SET NOCOUNT ON; SELECT 1 AS Connected;' -WorkingDirectory $script:RepoRoot

$databaseLiteral = ConvertTo-SqlLiteral -Value $Database
if ($Force) {
    if ($PSCmdlet.ShouldProcess("database '$Database' on '$Server'", 'drop and recreate')) {
        $forceQuery = @"
DECLARE @db sysname = $databaseLiteral;
DECLARE @sql nvarchar(max);
IF DB_ID(@db) IS NOT NULL
BEGIN
    SET @sql = N'ALTER DATABASE ' + QUOTENAME(@db) + N' SET SINGLE_USER WITH ROLLBACK IMMEDIATE;';
    EXEC sys.sp_executesql @sql;
    SET @sql = N'DROP DATABASE ' + QUOTENAME(@db) + N';';
    EXEC sys.sp_executesql @sql;
END;
SET @sql = N'CREATE DATABASE ' + QUOTENAME(@db) + N' COLLATE SQL_Latin1_General_CP1_CI_AS;';
EXEC sys.sp_executesql @sql;
"@
        Invoke-SqlcmdChecked -Description 'Drop and recreate database' -TargetDatabase 'master' -Query $forceQuery -WorkingDirectory $script:RepoRoot
    }
    else {
        throw 'Database recreation was not confirmed.'
    }
}
else {
    $createQuery = @"
DECLARE @db sysname = $databaseLiteral;
IF DB_ID(@db) IS NULL
BEGIN
    DECLARE @sql nvarchar(max) = N'CREATE DATABASE ' + QUOTENAME(@db) + N' COLLATE SQL_Latin1_General_CP1_CI_AS;';
    EXEC sys.sp_executesql @sql;
END;
"@
    Invoke-SqlcmdChecked -Description 'Create database if needed' -TargetDatabase 'master' -Query $createQuery -WorkingDirectory $script:RepoRoot
}

Invoke-SqlcmdChecked -Description 'Build generated schema' -TargetDatabase $Database -InputFile 'build_all.sql' -WorkingDirectory $sqlRoot -Variables @{ DatabaseName = $Database }

if ($SkipSeed) {
    Write-Host '==> Seed step skipped by -SkipSeed'
}
else {
    # The generated manifest :r's every per-table seed script, so only the manifest
    # is executed. Running the folder's scripts individually would load twice.
    $seedSqlRoot = Join-Path $sqlRoot '95_seed'
    $manifest = Join-Path $seedSqlRoot '00_seed_manifest.sql'
    if (Test-Path $manifest -PathType Leaf) {
        $resolvedSeedRoot = Resolve-SeedRoot
        Write-Host "    seed root (as the SQL Server sees it): $resolvedSeedRoot"
        Invoke-SqlcmdChecked -Description 'Seed data' -TargetDatabase $Database -InputFile '00_seed_manifest.sql' -WorkingDirectory $seedSqlRoot -Variables @{ SeedRoot = $resolvedSeedRoot }
    }
    else {
        Write-Host '==> No sql\95_seed\00_seed_manifest.sql found; skipping seed. Run scripts\rebuild_model.ps1 to generate it.'
    }
}

Invoke-SqlcmdChecked -Description 'Assert table count' -TargetDatabase $Database -InputFile $countScript -WorkingDirectory $PSScriptRoot

Write-Host ''
Write-Host 'Deployment summary:'
foreach ($summary in $script:PhaseSummaries) {
    Write-Host ("  {0,-32} {1,8:n1}s" -f $summary.Phase, $summary.Duration.TotalSeconds)
}
}
catch {
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
