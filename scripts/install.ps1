[CmdletBinding()]
param(
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install uv, then rerun .\scripts\install.ps1.'
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw 'Rust/Cargo is required. Install Rust, then rerun .\scripts\install.ps1.'
}

Write-Host '→ syncing the locked Python research environment'
& uv sync --project $RepoRoot --extra dev --locked
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed with exit code $LASTEXITCODE"
}

Write-Host '→ building the Rust terminal client'
& cargo build --manifest-path (Join-Path $RepoRoot 'apps\mathagent-tui\Cargo.toml') --release --locked
if ($LASTEXITCODE -ne 0) {
    throw "cargo build failed with exit code $LASTEXITCODE"
}

Write-Host 'MathAgent is installed.' -ForegroundColor Green
if ($Launch) {
    & (Join-Path $ScriptDir 'start.ps1')
} else {
    Write-Host 'Start it with: .\scripts\start.ps1'
    Write-Host 'Or start immediately with: .\scripts\install.ps1 -Launch'
}
