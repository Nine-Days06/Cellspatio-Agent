<#
Prepare a thin pack: install Python dependencies, then check R/Seurat.

This does not install R itself (needs admin). It prints the commands instead.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $root "app"
$venv = Join-Path $app ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

$uv = Get-Command uv -ErrorAction SilentlyContinue
$python = Get-Command python -ErrorAction SilentlyContinue
if ($uv) {
    & uv venv $venv
    if ($LASTEXITCODE -ne 0) { throw "uv venv failed with exit code $LASTEXITCODE" }
    & uv pip install --python $venvPython -r (Join-Path $app "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed with exit code $LASTEXITCODE" }
} elseif ($python) {
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed with exit code $LASTEXITCODE" }
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed with exit code $LASTEXITCODE" }
    & $venvPython -m pip install -r (Join-Path $app "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }
} else {
    Write-Error "Install Python 3.10+ or uv first: https://www.python.org/downloads/"
    exit 1
}

$rscript = $null
if ($env:R_HOME -and (Test-Path -LiteralPath (Join-Path $env:R_HOME "bin\Rscript.exe"))) {
    $rscript = Join-Path $env:R_HOME "bin\Rscript.exe"
} else {
    $cmd = Get-Command Rscript -ErrorAction SilentlyContinue
    if ($cmd) { $rscript = $cmd.Source }
}
if (-not $rscript -and $env:OS -eq "Windows_NT") {
    # 与 r_executor 一致：R 通常写注册表，未加 PATH 也能找到
    foreach ($key in @("HKLM:\Software\R-core\R", "HKCU:\Software\R-core\R")) {
        $installPath = (Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue).InstallPath
        $candidate = if ($installPath) { Join-Path $installPath "bin\Rscript.exe" }
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            $rscript = $candidate
            break
        }
    }
}
$installHint = "Rscript -e `"install.packages(c('Seurat','uwot','remotes'), repos='https://cloud.r-project.org'); remotes::install_github('immunogenomics/presto')`""
if (-not $rscript) {
    Write-Warning "Rscript not found. Install R 4.6.x: https://cran.r-project.org/bin/windows/base/"
    Write-Warning $installHint
    exit 2
}

$check = & $rscript -e "cat(getRversion() >= '4.6' && all(sapply(c('Seurat','presto','uwot'), requireNamespace, quietly=TRUE)))"
if ($LASTEXITCODE -ne 0 -or (($check -join "").Trim() -ne "TRUE")) {
    Write-Warning "R 4.6.x with Seurat, presto and uwot is required."
    Write-Warning $installHint
    exit 2
}

Write-Host "Thin pack ready. Run launch.ps1."
