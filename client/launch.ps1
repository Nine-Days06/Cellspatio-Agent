<#
Start the CellSpatio local service from a portable folder.

Layout:
  app/      source, skills, web/dist (required)
  runtime/  optional: python/, venv/, R/
Data lives in <folder>\data unless CELLSPATIO_HOME is set.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $root "app"
$runtime = Join-Path $root "runtime"

$data = if ($env:CELLSPATIO_HOME) { $env:CELLSPATIO_HOME } else { Join-Path $root "data" }
New-Item -ItemType Directory -Force -Path $data | Out-Null
# R/Python temp files stay with project data instead of the user's temp folder
$tmp = Join-Path $data "tmp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:TEMP = $tmp
$env:TMP = $tmp

$python = @(
    (Join-Path $runtime "python\python.exe"),
    (Join-Path $app ".venv\Scripts\python.exe"),
    (Join-Path $runtime "venv\Scripts\python.exe")
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source }
}
if (-not $python) {
    Write-Error "Python not found. Run bootstrap.ps1 in this folder first."
    exit 1
}

$bundledR = Join-Path $runtime "R"
if (Test-Path -LiteralPath (Join-Path $bundledR "bin\Rscript.exe")) {
    $env:R_HOME = (Resolve-Path -LiteralPath $bundledR).Path
}

$env:CELLSPATIO_LOCAL = "1"
$env:PYTHONPATH = $app
Set-Location -LiteralPath $data
& $python -m src.api
