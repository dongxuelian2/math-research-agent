[CmdletBinding()]
param(
    [string]$Project = 'main',
    [string]$Source = ''
)

$MathRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runner = Join-Path $MathRoot 'run_math_agent.ps1'
& $Runner -Command import -Project $Project -Source $Source
exit $LASTEXITCODE

