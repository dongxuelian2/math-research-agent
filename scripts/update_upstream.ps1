[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$MathRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Repo = Join-Path $MathRoot 'openprover'

git -c safe.directory=E:/tool/math/openprover -C $Repo fetch upstream
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host 'Fetched upstream without modifying the customization branch.'
Write-Host 'Review changes with:'
Write-Host "  git -C $Repo log --oneline --left-right math-research-custom...upstream/master"
Write-Host 'After review, merge or rebase manually; keep private projects outside the OpenProver repository.'

