<#
Run unit tests with a project-local pytest temp directory.

The default Windows user temp directory can reject pytest's ACL changes. This
script also starts the embedded PostgreSQL on demand and stops only the
instance it started.
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "python",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$pgctl = Join-Path $root "runtime\pgsql\bin\pg_ctl.exe"
$data = Join-Path $root "runtime\pgdata"
$startPg = Join-Path $root "start_pg.ps1"

if (-not (Test-Path $pgctl)) { throw "missing: $pgctl" }

$basetemp = Join-Path $root (".pytest_tmp\pytest-unit-" + (Get-Date -Format "yyyyMMdd-HHmmssfff"))
New-Item -ItemType Directory -Force -Path $basetemp | Out-Null

& $pgctl status -D $data *> $null
$startedPg = $LASTEXITCODE -ne 0
if ($startedPg) {
    & $startPg
    if ($LASTEXITCODE -ne 0) {
        & $startPg -Stop
        throw "failed to start PostgreSQL"
    }
}

$exitCode = 0
try {
    & $PythonExe -m pytest tests/unit/ -q --basetemp $basetemp @PytestArgs
    $exitCode = $LASTEXITCODE
}
finally {
    if ($startedPg) { & $startPg -Stop }
}

exit $exitCode
