[CmdletBinding()]
param(
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

Write-Host "=== Math Research Agent Bootstrap ===" -ForegroundColor Cyan
Write-Host "Repository root: $RepoRoot"

# 1. Locate a compatible Python interpreter (>= 3.10)
$CandidatePythons = @()
if ($Python) {
    $CandidatePythons += $Python
}
$CandidatePythons += @('py', 'python', 'python3')

$FoundPython = $null
foreach ($Candidate in $CandidatePythons) {
    try {
        $VersionCheck = & $Candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}'); sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $VersionCheck) {
            $FoundPython = $Candidate
            Write-Host "Found compatible Python ($VersionCheck) using '$Candidate'." -ForegroundColor Green
            break
        }
    } catch {
        # Try next candidate
    }
}

if (-not $FoundPython) {
    throw "Error: No compatible Python (>= 3.10) found. Please install Python 3.10+ and ensure it is available in PATH or provide -Python path."
}

# 2. Setup Virtual Environment
$VenvDir = Join-Path $RepoRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating virtual environment at '$VenvDir'..." -ForegroundColor Yellow
    & $FoundPython -m venv $VenvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $VenvPython)) {
        throw "Failed to create virtual environment at $VenvDir."
    }
    Write-Host "Virtual environment created successfully." -ForegroundColor Green
} else {
    Write-Host "Existing virtual environment found at '$VenvDir'." -ForegroundColor Green
}

# 3. Upgrade pip and install package + test dependencies
Write-Host "Installing openprover and required dependencies..." -ForegroundColor Yellow
$OpenProverDir = Join-Path $RepoRoot 'openprover'

& $VenvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Warning "pip upgrade encountered a non-fatal warning; continuing..."
}

& $VenvPython -m pip install -e $OpenProverDir --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install openprover in editable mode."
}

& $VenvPython -m pip install pytest --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install pytest dependency."
}

# 4. Verify installation
Write-Host "Verifying package import..." -ForegroundColor Yellow
& $VenvPython -c "import openprover; import openprover.math_research; print('Verification: openprover and openprover.math_research imported successfully.')"
if ($LASTEXITCODE -ne 0) {
    throw "Package verification failed."
}

Write-Host "`n=== Bootstrap Completed Successfully! ===" -ForegroundColor Cyan
Write-Host "You can now run the zero-cost mock demo and tests with:"
Write-Host "  1. Check demo status:" -ForegroundColor White
Write-Host "     .\run_math_agent.ps1 -Command status -Project demo" -ForegroundColor Yellow
Write-Host "  2. Run test suite:" -ForegroundColor White
Write-Host "     .\.venv\Scripts\python.exe -m pytest -q openprover\tests\math_research" -ForegroundColor Yellow
