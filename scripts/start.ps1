[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$Binary = Join-Path $RepoRoot 'apps\mathagent-tui\target\release\mathagent-tui.exe'

if (-not (Test-Path $Binary)) {
    throw 'MathAgent is not installed. Run .\scripts\install.ps1 first.'
}

& $Binary --root $RepoRoot @Arguments
exit $LASTEXITCODE
