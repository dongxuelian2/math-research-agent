[CmdletBinding()]
param(
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Node.js 22+ is required. Install Node.js, then rerun .\scripts\install.ps1.'
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw 'pnpm is required. Install pnpm, then rerun .\scripts\install.ps1.'
}

Write-Host '→ installing the locked TypeScript workspace'
& pnpm install --dir $RepoRoot --frozen-lockfile
if ($LASTEXITCODE -ne 0) {
    throw "pnpm install failed with exit code $LASTEXITCODE"
}

Write-Host '→ building the TypeScript proof core and standalone GUI'
& pnpm run --dir $RepoRoot build
if ($LASTEXITCODE -ne 0) {
    throw "pnpm build failed with exit code $LASTEXITCODE"
}

Write-Host 'Math Research Agent is installed.' -ForegroundColor Green
if ($Launch) {
    & (Join-Path $ScriptDir 'start.ps1')
} else {
    Write-Host 'Start it with: .\scripts\start.ps1'
    Write-Host 'Or start immediately with: .\scripts\install.ps1 -Launch'
}
