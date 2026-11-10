param(
    [ValidateSet('0','1','2','3','all','legacy')][string]$Stage='all',
    [int]$Scenarios=40,
    [switch]$Test
)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$TaskPython=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $TaskPython)) { throw '请先用 Python 3.12 创建 .venv 并安装 requirements-lock.txt' }
if ($Test) {
    & $TaskPython -m pytest -q --junitxml=results/summary/tests.xml
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if ($Stage -eq 'legacy') {
    & $TaskPython sim/reference/pipeline_v2.py
} else {
    & $TaskPython sim/experiments.py --stage $Stage --scenarios $Scenarios
}
exit $LASTEXITCODE
