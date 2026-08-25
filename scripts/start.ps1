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

$CredentialFile = Join-Path $RepoRoot '11111.json'
if ([string]::IsNullOrWhiteSpace($env:GOOGLE_APPLICATION_CREDENTIALS) -and (Test-Path $CredentialFile)) {
  $env:GOOGLE_APPLICATION_CREDENTIALS = $CredentialFile
}

& pnpm --dir $RepoRoot start -- @Arguments
exit $LASTEXITCODE
