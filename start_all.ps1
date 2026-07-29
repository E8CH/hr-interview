chcp 65001 | Out-Null
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

Write-Host ""
Write-Host "=== HR Interview System - start ===" -ForegroundColor Cyan
Write-Host ("Root: {0}" -f $root) -ForegroundColor Gray

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$venvUiPython = Join-Path $root ".venv-ui\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: .venv\Scripts\python.exe not found" -ForegroundColor Red
    exit 1
}

$services = @(
    @{name="01-version-manager";    port=8001; label="Version-Manager"},
    @{name="02-distributor";        port=8002; label="Distributor"},
    @{name="03-response-collector"; port=8003; label="Response-Collector"},
    @{name="04-scheduler";          port=8004; label="Scheduler"},
    @{name="05-repair-engine";      port=8005; label="Repair-Engine"},
    @{name="06-notification-hub";   port=8006; label="Notification-Hub"},
    @{name="07-audit-analytics";    port=8007; label="Audit-Analytics"}
)

Write-Host ""
Write-Host "[1/3] Opening 7 service windows..." -ForegroundColor Yellow

foreach ($svc in $services) {
    $svcPath = Join-Path $root ("services\" + $svc.name)
    $mainPy = Join-Path $svcPath "app\main.py"
    if (-not (Test-Path $mainPy)) {
        Write-Host ("  SKIP {0} (main.py missing)" -f $svc.name) -ForegroundColor Yellow
        continue
    }
    $title = "{0} :{1}" -f $svc.label, $svc.port
    $inner = "`$Host.UI.RawUI.WindowTitle='$title'; chcp 65001 | Out-Null; Set-Location '$svcPath'; Write-Host '=== $($svc.label) port $($svc.port) ===' -ForegroundColor Cyan; & '$venvPython' -m uvicorn app.main:app --port $($svc.port) --reload; Write-Host 'Press Enter to close'; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$inner | Out-Null
    Write-Host ("  START {0} - port {1}" -f $svc.label, $svc.port) -ForegroundColor Green
    Start-Sleep -Milliseconds 500
}

Write-Host ""
Write-Host "[2/3] Health check (max 40s)..." -ForegroundColor Yellow

$elapsed = 0
$allUp = $false
while ($elapsed -lt 40 -and -not $allUp) {
    Start-Sleep -Seconds 2
    $elapsed += 2
    $upCount = 0
    $upList = @()
    foreach ($svc in $services) {
        try {
            $resp = Invoke-WebRequest -Uri ("http://localhost:{0}/healthz" -f $svc.port) -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                $upCount++
                $upList += $svc.port
            }
        }
        catch { }
    }
    Write-Host ("  {0}/{1} OK ({2}s) [{3}]" -f $upCount, $services.Count, $elapsed, ($upList -join ",")) -ForegroundColor Gray
    if ($upCount -eq $services.Count) { $allUp = $true }
}

if ($allUp) {
    Write-Host "  All services OK" -ForegroundColor Green
}
else {
    Write-Host "  Some failed - check each window" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[3/3] Streamlit..." -ForegroundColor Yellow

if (Test-Path $venvUiPython) {
    $uiCmd = "`$Host.UI.RawUI.WindowTitle='Streamlit :8501'; chcp 65001 | Out-Null; Set-Location '$root'; & '$venvUiPython' -m streamlit run tools\test_console.py; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$uiCmd | Out-Null
    Write-Host "  Streamlit http://localhost:8501" -ForegroundColor Green
}
else {
    Write-Host "  .venv-ui missing - skip Streamlit" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Done. Stop: .\stop_all.ps1" -ForegroundColor Green
