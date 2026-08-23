[CmdletBinding()]
param(
    [switch]$RunTests
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install uv, then rerun .\scripts\bootstrap.ps1.'
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $RepoRoot '.uv-cache'
}
if (-not $env:UV_LINK_MODE) {
    $env:UV_LINK_MODE = 'copy'
}

Write-Host '→ syncing the locked development environment'
& uv sync --project $RepoRoot --extra dev --locked
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed with exit code $LASTEXITCODE"
}

Write-Host '→ verifying package imports'
& uv run --project $RepoRoot python -c "import math_research_agent; import math_research_agent.research; print('math_research_agent import: OK')"
if ($LASTEXITCODE -ne 0) {
    throw "package import verification failed with exit code $LASTEXITCODE"
}

if ($RunTests) {
    Write-Host '→ running deterministic test suite'
    & uv run --project $RepoRoot pytest -q
    if ($LASTEXITCODE -ne 0) {
        throw "pytest failed with exit code $LASTEXITCODE"
    }
}

Write-Host 'Windows bootstrap completed successfully.' -ForegroundColor Green
Write-Host 'Try: .\run_math_agent.ps1 -Command status -Project demo'
