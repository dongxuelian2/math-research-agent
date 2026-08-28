[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir

if (-not (Test-Path (Join-Path $RepoRoot 'backend\dist\src\index.js'))) {
  throw 'Math Research Agent is not installed. Run: .\scripts\install.ps1 first.'
}

& pnpm --dir $RepoRoot start -- @Arguments
exit $LASTEXITCODE
