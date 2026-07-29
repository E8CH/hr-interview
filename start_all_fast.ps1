chcp 65001 | Out-Null
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

Write-Host ""
Write-Host "=== HR System - FAST START ===" -ForegroundColor Cyan

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$venvUiPython = Join-Path $root ".venv-ui\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: venv missing" -ForegroundColor Red
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

# [1/3] 병렬 창 오픈 - 대기 없음, --reload 제거
$startTime = Get-Date
Write-Host "[1/3] Opening 7 windows in parallel..." -ForegroundColor Yellow

foreach ($svc in $services) {
    $svcPath = Join-Path $root ("services\" + $svc.name)
    $mainPy = Join-Path $svcPath "app\main.py"
    if (-not (Test-Path $mainPy)) { continue }
    $title = "{0} :{1}" -f $svc.label, $svc.port
    # --reload 제거, --workers 1 명시로 빠른 기동
    $inner = "`$Host.UI.RawUI.WindowTitle='$title'; chcp 65001 | Out-Null; Set-Location '$svcPath'; & '$venvPython' -m uvicorn app.main:app --port $($svc.port) --workers 1; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$inner | Out-Null
}
Write-Host ("  All 7 windows launched in {0:N1}s" -f ((Get-Date) - $startTime).TotalSeconds) -ForegroundColor Green

# [2/3] 빠른 헬스체크 - 0.3초 간격
Write-Host "[2/3] Health check (0.3s interval, max 15s)..." -ForegroundColor Yellow
$elapsed = 0
$allUp = $false
while ($elapsed -lt 15 -and -not $allUp) {
    Start-Sleep -Milliseconds 300
    $elapsed += 0.3
    $upCount = 0
    foreach ($svc in $services) {
        try {
            $r = Invoke-WebRequest -Uri ("http://localhost:{0}/healthz" -f $svc.port) -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $upCount++ }
        } catch { }
    }
    if ($upCount -eq $services.Count) {
        $allUp = $true
        Write-Host ("  All 7 OK in {0:N1}s" -f $elapsed) -ForegroundColor Green
        break
    }
    Write-Host ("  {0}/{1} ({2:N1}s)" -f $upCount, $services.Count, $elapsed) -ForegroundColor Gray
}
if (-not $allUp) { Write-Host "  Some failed" -ForegroundColor Yellow }

# [3/3] Streamlit
Write-Host "[3/3] Streamlit..." -ForegroundColor Yellow
if (Test-Path $venvUiPython) {
    $uiCmd = "`$Host.UI.RawUI.WindowTitle='Streamlit :8501'; Set-Location '$root'; & '$venvUiPython' -m streamlit run tools\test_console.py; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$uiCmd | Out-Null
    Write-Host "  Streamlit http://localhost:8501" -ForegroundColor Green
}

$total = ((Get-Date) - $startTime).TotalSeconds
Write-Host ""
Write-Host ("Total time: {0:N1}s" -f $total) -ForegroundColor Cyan
