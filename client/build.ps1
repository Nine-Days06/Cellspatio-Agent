<#
Build the portable folder and online/bundled-runtime zips.

  .\build.ps1                    # online: app code only
  .\build.ps1 -Bundled           # bundled runtime: CPython + R into runtime/
  .\build.ps1 -Bundled -NoZip    # folder only, faster for local checks

Bundled runtime needs an R install: pass -RRuntime <dir> or set R_HOME.
Python defaults to a uv-managed CPython 3.13; override with -PythonRuntime <dir>.
#>
[CmdletBinding()]
param(
    [Alias("Fat")]
    [switch]$Bundled,
    [string]$PythonRuntime = "",
    [string]$RRuntime = "",
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "dist\client"
$stage = Join-Path $out "cellspatio"
$app = Join-Path $stage "app"

function Copy-Tree {
    param([string]$Source, [string]$Destination, [string[]]$ExcludeDirs = @())
    $cmd = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    if ($ExcludeDirs.Count -gt 0) { $cmd += "/XD"; $cmd += $ExcludeDirs }
    & robocopy @cmd | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $Source" }
}

if (-not (Test-Path -LiteralPath (Join-Path $root "web\dist\index.html"))) {
    throw "web/dist is missing; run 'npm run build' in web/ first."
}

New-Item -ItemType Directory -Force -Path $out | Out-Null
if (Test-Path -LiteralPath $stage) {
    if (Test-Path -LiteralPath (Join-Path $stage "data")) {
        throw "Refusing to rebuild: client data exists at $(Join-Path $stage 'data'); move it first."
    }
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $app | Out-Null

Copy-Tree (Join-Path $root "src") (Join-Path $app "src") @("__pycache__")
Copy-Tree (Join-Path $root "skills") (Join-Path $app "skills") @("__pycache__")
Copy-Tree (Join-Path $root "web\dist") (Join-Path $app "web\dist")
Copy-Item (Join-Path $root "pyproject.toml"), (Join-Path $root "requirements.txt") $app
Copy-Item (Join-Path $PSScriptRoot "launch.ps1"), (Join-Path $PSScriptRoot "launch.cmd"), (Join-Path $PSScriptRoot "bootstrap.ps1"), (Join-Path $PSScriptRoot "README.txt") $stage

if ($Bundled) {
    $runtime = Join-Path $stage "runtime"
    $uv = Get-Command uv -ErrorAction Stop
    if (-not $RRuntime) {
        if (-not $env:R_HOME) {
            throw "Pass -RRuntime <R install> or set R_HOME for a bundled-runtime build."
        }
        $RRuntime = $env:R_HOME
    }
    if (-not $PythonRuntime) {
        & uv python install 3.13
        if ($LASTEXITCODE -ne 0) { throw "uv python install failed with exit code $LASTEXITCODE" }
        $found = (& uv python find --managed-python 3.13).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $found) { throw "uv python find failed" }
        $PythonRuntime = Split-Path -Parent $found
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PythonRuntime "python.exe"))) {
        throw "PythonRuntime must contain python.exe: $PythonRuntime"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $RRuntime "bin\Rscript.exe"))) {
        throw "RRuntime must contain bin\Rscript.exe: $RRuntime"
    }
    Copy-Tree $PythonRuntime (Join-Path $runtime "python")
    & uv pip install --python (Join-Path $runtime "python\python.exe") --break-system-packages -r (Join-Path $app "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }
    Copy-Tree $RRuntime (Join-Path $runtime "R") @("doc", "tests", "src")
    $env:R_HOME = (Resolve-Path -LiteralPath (Join-Path $runtime "R")).Path
    $env:TEMP = Join-Path $out "build-tmp"
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
    & (Join-Path $runtime "R\bin\Rscript.exe") -e "stopifnot(getRversion() >= '4.6', all(sapply(c('Seurat','presto','uwot'), requireNamespace, quietly=TRUE)))"
    if ($LASTEXITCODE -ne 0) { throw "Bundled R is missing R>=4.6 or Seurat/presto/uwot" }
}

if (-not $NoZip) {
    $kind = if ($Bundled) { "bundled" } else { "online" }
    $zip = Join-Path $out "cellspatio-$kind.zip"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    & tar -a -c -f $zip -C $stage .
    if ($LASTEXITCODE -ne 0) { throw "tar failed with exit code $LASTEXITCODE" }
}

Write-Host "Built $stage"
