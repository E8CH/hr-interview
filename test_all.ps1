chcp 65001 | Out-Null
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
$py = Join-Path $root ".venv\Scripts\python.exe"
$runner = Join-Path $root "tools\test_runner.py"

Write-Host ""
Write-Host "[1/3] health" -ForegroundColor Yellow
& $py $runner health

Write-Host ""
Write-Host "[2/3] happy scenario" -ForegroundColor Yellow
& $py $runner scenario happy

Write-Host ""
Write-Host "[3/3] db" -ForegroundColor Yellow
& $py $runner db

Write-Host ""
Write-Host "Done." -ForegroundColor Green
