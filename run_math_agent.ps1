[CmdletBinding()]
param(
    [ValidateSet(
        'run', 'import', 'context', 'status', 'provider-smoke',
        'campaign-run', 'campaign-status', 'campaign-stop', 'campaign-resume'
    )]
    [string]$Command = 'run',
    [string]$Project = 'demo',
    [string]$Target = '',
    [ValidateRange(0, 64)]
    [int]$WorkerCount = 0,
    [string]$Config = '',
    [string]$Resume = '',
    [switch]$DryRun,
    [string]$Source = '',
    [string]$Role = 'auditor',
    [string]$Expect = 'OPENAI_PROVIDER_OK',
    [switch]$ExpandContext,
    [ValidateSet('', 'context', 'candidate', 'audits')]
    [string]$StopAfter = '',
    [ValidateSet('normal', 'overnight')]
    [string]$Profile = 'normal',
    [string]$Campaign = '',
    [string]$Reason = '',
    [string]$ReplayManifest = '',
    [ValidateRange(-1, 100)]
    [int]$MaxRepairCycles = -1,
    [switch]$StopAfterCheckpoint
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$MathRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $MathRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found at '$Python'. Run '.\scripts\bootstrap.ps1' to set up the environment."
}

if ([System.IO.Path]::IsPathRooted($Project)) {
    $ProjectPath = $Project
} else {
    $ProjectPath = Join-Path (Join-Path $MathRoot 'projects') $Project
}
if (-not $Config) {
    if ($Command -eq 'provider-smoke') {
        $Config = Join-Path $MathRoot 'configs\models.openai.example.json'
    } else {
        $Config = Join-Path $MathRoot 'configs\models.mock.json'
    }
} elseif (-not [System.IO.Path]::IsPathRooted($Config)) {
    $Config = Join-Path $MathRoot $Config
}
if ($ReplayManifest -and -not [System.IO.Path]::IsPathRooted($ReplayManifest)) {
    $ReplayManifest = Join-Path $MathRoot $ReplayManifest
}

$CliArgs = @('-m', 'openprover.math_research', $Command)
if ($Command -ne 'provider-smoke') {
    $CliArgs += @('--project', $ProjectPath)
}
switch ($Command) {
    'run' {
        if (-not $Target) { throw '-Target is required for run.' }
        if ($WorkerCount -eq 0) { $WorkerCount = 3 }
        $CliArgs += @('--target', $Target, '--workers', $WorkerCount, '--config', $Config)
        if ($Resume) { $CliArgs += @('--resume', $Resume) }
        if ($DryRun) { $CliArgs += '--dry-run' }
        if ($ExpandContext) { $CliArgs += '--expand-context' }
        if ($StopAfter) { $CliArgs += @('--stop-after', $StopAfter) }
    }
    'import' {
        if (-not $Source) { $Source = Join-Path $ProjectPath 'inbox' }
        $CliArgs += @('--source', $Source)
    }
    'context' {
        if (-not $Target) { throw '-Target is required for context.' }
        $Output = Join-Path $ProjectPath "runs\manual-context-$Target"
        $CliArgs += @('--target', $Target, '--output', $Output)
        if ($ExpandContext) { $CliArgs += '--expand' }
    }
    'status' {
        if ($Target) { $CliArgs += @('--target', $Target) }
    }
    'provider-smoke' {
        $Output = Join-Path $MathRoot 'logs\provider-smoke'
        $CliArgs += @(
            '--config', $Config,
            '--role', $Role,
            '--output', $Output,
            '--expect', $Expect
        )
    }
    'campaign-run' {
        if (-not $Target) { throw '-Target is required for campaign-run.' }
        $CliArgs += @('--target', $Target, '--config', $Config, '--profile', $Profile)
        if ($Campaign) { $CliArgs += @('--campaign-id', $Campaign) }
        if ($WorkerCount -gt 0) { $CliArgs += @('--workers', $WorkerCount) }
        if ($MaxRepairCycles -ge 0) {
            $CliArgs += @('--max-repair-cycles', $MaxRepairCycles)
        }
        if ($ReplayManifest) {
            $CliArgs += @('--replay-manifest', $ReplayManifest)
        }
        if ($StopAfterCheckpoint) { $CliArgs += '--stop-after-checkpoint' }
    }
    'campaign-status' {
        if (-not $Campaign) { throw '-Campaign is required for campaign-status.' }
        $CliArgs += @('--campaign', $Campaign)
    }
    'campaign-stop' {
        if (-not $Campaign) { throw '-Campaign is required for campaign-stop.' }
        if (-not $Reason) { throw '-Reason is required for campaign-stop.' }
        $CliArgs += @('--campaign', $Campaign, '--reason', $Reason)
    }
    'campaign-resume' {
        if (-not $Campaign) { throw '-Campaign is required for campaign-resume.' }
        $CliArgs += @('--campaign', $Campaign, '--config', $Config)
        if ($WorkerCount -gt 0) { $CliArgs += @('--workers', $WorkerCount) }
        if ($StopAfterCheckpoint) { $CliArgs += '--stop-after-checkpoint' }
    }
}

& $Python @CliArgs
exit $LASTEXITCODE
