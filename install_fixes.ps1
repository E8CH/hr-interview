# HR Interview System - Fix Package Installer (ASCII only)
chcp 65001 | Out-Null
$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=== HR Interview - Fix Package Install ===" -ForegroundColor Cyan

# [1/3] Replace tools files
Write-Host ""
Write-Host "[1/3] Replace tools/ files" -ForegroundColor Yellow
if (Test-Path "tools\test_console.py") {
    Copy-Item "tools\test_console.py" "tools\test_console.py.bak" -Force
    Write-Host "  backup: tools\test_console.py.bak" -ForegroundColor Gray
}
if (Test-Path "tools\test_runner.py") {
    Copy-Item "tools\test_runner.py" "tools\test_runner.py.bak" -Force
    Write-Host "  backup: tools\test_runner.py.bak" -ForegroundColor Gray
}

if (-not (Test-Path "fix_tools\test_console.py")) {
    Write-Host "  ERROR: fix_tools not found. Extract fix_package.zip first." -ForegroundColor Red
    exit 1
}

Move-Item -Force "fix_tools\test_console.py" "tools\test_console.py"
Move-Item -Force "fix_tools\test_runner.py"  "tools\test_runner.py"
Write-Host "  OK: tools replaced" -ForegroundColor Green

# [2/3] Deploy BFF
Write-Host ""
Write-Host "[2/3] Deploy BFF folder" -ForegroundColor Yellow
if (-not (Test-Path "bff\app")) {
    New-Item -ItemType Directory -Path "bff\app" -Force | Out-Null
}
Copy-Item -Force "fix_bff\app\main.py"      "bff\app\main.py"
Copy-Item -Force "fix_bff\app\clients.py"   "bff\app\clients.py"
Copy-Item -Force "fix_bff\app\workflows.py" "bff\app\workflows.py"
Copy-Item -Force "fix_bff\app\__init__.py"  "bff\app\__init__.py"
Copy-Item -Force "fix_bff\requirements.txt" "bff\requirements.txt"
Write-Host "  OK: bff deployed" -ForegroundColor Green

# [3/3] httpx check
Write-Host ""
Write-Host "[3/3] Verify httpx" -ForegroundColor Yellow
& ".\.venv\Scripts\python.exe" -m pip install "httpx>=0.28.0" --quiet
Write-Host "  OK: httpx ready" -ForegroundColor Green

Remove-Item -Recurse -Force "fix_tools" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "fix_bff"   -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Install Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1) .\stop_all.ps1"
Write-Host "  2) .\start_all.ps1"
Write-Host "  3) New window - cd bff; ..\.venv\Scripts\Activate.ps1; uvicorn app.main:app --port 8000 --reload"
Write-Host "  4) Browser: http://localhost:8000/docs"
Write-Host "  5) Streamlit: press R to Rerun"
