<#
Start/stop the project's embedded PostgreSQL (port 5433).

Usage:
    .\start_pg.ps1          # start if not running, then verify "rag" is reachable
    .\start_pg.ps1 -Stop    # fast shutdown

Port must be passed on the command line: postgresql.conf keeps the default 5432,
which is used by another local instance.
#>
[CmdletBinding()]
param([switch]$Stop)

$root = $PSScriptRoot
$pgctl = Join-Path $root 'runtime\pgsql\bin\pg_ctl.exe'
$psql = Join-Path $root 'runtime\pgsql\bin\psql.exe'
$data = Join-Path $root 'runtime\pgdata'
$log = Join-Path $data 'pg.log'
$port = 5433

foreach ($path in @($pgctl, $psql, $data)) {
    if (-not (Test-Path $path)) { throw "missing: $path" }
}

& $pgctl status -D $data *> $null
$running = $LASTEXITCODE -eq 0

if ($Stop) {
    if (-not $running) { Write-Host 'PostgreSQL is not running.'; exit 0 }
    & $pgctl stop -D $data -m fast -w
    return
}

if ($running) {
    Write-Host "PostgreSQL already running (port $port)."
}
else {
    & $pgctl start -D $data -o "-p $port" -l $log -w -t 60
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Start failed. Last log lines (may come from a previous run):'
        Get-Content $log -Tail 15 -ErrorAction SilentlyContinue
        exit 1
    }
}

& $psql -h 127.0.0.1 -p $port -U postgres -d rag -tAc 'select 1' | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: rag reachable at 127.0.0.1:$port"
}
else {
    Write-Host "WARN: server is up but database 'rag' is not reachable"
    exit 1
}
