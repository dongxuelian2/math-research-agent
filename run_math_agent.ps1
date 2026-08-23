[CmdletBinding()]
param(
    [ValidateSet(
        'run', 'import', 'context', 'status', 'provider-smoke', 'formalize',
        'campaign-run', 'campaign-status', 'campaign-stop', 'campaign-resume',
        'demo', 'observatory', 'benchmark'
    )]
    [string]$Command = 'status',
    [string]$Project = 'demo',
    [string]$Target = '',
    [ValidateRange(0, 64)]
    [int]$WorkerCount = 0,
    [string]$Config = '',
    [string]$Resume = '',
    [switch]$DryRun,
    [string]$Source = '',
    [string]$Role = 'final_proof_auditor',
    [string]$Expect = 'GEMINI_PROVIDER_OK',
    [switch]$ExpandContext,
    [ValidateSet('', 'context', 'candidate', 'audits')]
    [string]$StopAfter = '',
    [ValidateSet('normal', 'overnight')]
    [string]$Profile = 'normal',
    [string]$Campaign = '',
    [string]$Reason = '',
    [string]$ReplayManifest = '',
    [string]$Run = '',
    [ValidateRange(-1, 100)]
    [int]$MaxRepairCycles = -1,
    [switch]$StopAfterCheckpoint,
    [string]$HostName = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:UV_CACHE_DIR) {
    $env:UV_CACHE_DIR = Join-Path $RepoRoot '.uv-cache'
}
if (-not $env:UV_LINK_MODE) {
    $env:UV_LINK_MODE = 'copy'
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Run .\scripts\bootstrap.ps1 after installing uv.'
}

if ([System.IO.Path]::IsPathRooted($Project)) {
    $ProjectPath = $Project
} else {
    $ProjectPath = Join-Path (Join-Path $RepoRoot 'projects') $Project
}
if (-not $Config) {
    $Config = Join-Path $RepoRoot 'configs\models.mock.json'
} elseif (-not [System.IO.Path]::IsPathRooted($Config)) {
    $Config = Join-Path $RepoRoot $Config
}
if ($ReplayManifest -and -not [System.IO.Path]::IsPathRooted($ReplayManifest)) {
    $ReplayManifest = Join-Path $RepoRoot $ReplayManifest
}

$CliArgs = @('-m', 'math_research_agent.research', $Command)
if ($Command -notin @('provider-smoke', 'benchmark')) {
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
        $CliArgs += @('--target', $Target)
        if ($ExpandContext) { $CliArgs += '--expand' }
    }
    'status' {
        if ($Target) { $CliArgs += @('--target', $Target) }
    }
    'provider-smoke' {
        $Output = Join-Path $RepoRoot 'logs\provider-smoke'
        $CliArgs += @('--config', $Config, '--role', $Role, '--output', $Output, '--expect', $Expect)
    }
    'formalize' {
        if (-not $Target) { throw '-Target is required for formalize.' }
        if (-not $Run) { throw '-Run is required for formalize.' }
        $CliArgs += @('--target', $Target, '--config', $Config, '--run', $Run)
    }
    'campaign-run' {
        if (-not $Target) { throw '-Target is required for campaign-run.' }
        $CliArgs += @('--target', $Target, '--config', $Config, '--profile', $Profile)
        if ($Campaign) { $CliArgs += @('--campaign-id', $Campaign) }
        if ($WorkerCount -gt 0) { $CliArgs += @('--workers', $WorkerCount) }
        if ($MaxRepairCycles -ge 0) { $CliArgs += @('--max-repair-cycles', $MaxRepairCycles) }
        if ($ReplayManifest) { $CliArgs += @('--replay-manifest', $ReplayManifest) }
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
    'observatory' {
        $CliArgs += @('--host', $HostName, '--port', $Port)
    }
}
$CliArgs += $ExtraArgs

& uv run --project $RepoRoot python @CliArgs
exit $LASTEXITCODE
