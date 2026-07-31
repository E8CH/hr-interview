chcp 65001 | Out-Null
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

Write-Host ""
Write-Host "=== HR System - ULTRA FAST START ===" -ForegroundColor Cyan

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
    $inner = "`$Host.UI.RawUI.WindowTitle='$title'; chcp 65001 | Out-Null; Set-Location '$svcPath'; & '$venvPython' -m uvicorn app.main:app --port $($svc.port) --workers 1; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$inner | Out-Null
}
Write-Host ("  Launched in {0:N1}s" -f ((Get-Date) - $startTime).TotalSeconds) -ForegroundColor Green

# [2/3] TCP 포트 스캔 (병렬) - Invoke-WebRequest보다 10배 빠름
Write-Host "[2/3] TCP port check (parallel, 0.2s interval)..." -ForegroundColor Yellow

$hcStart = Get-Date
$maxWait = 20
$elapsed = 0
$allUp = $false

# TCP 소켓 재사용 - .NET 직접 호출
function Test-PortFast {
    param([int]$Port, [int]$TimeoutMs = 200)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $success = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if ($success -and $client.Connected) {
            $client.EndConnect($iar)
            return $true
        }
        return $false
    }
    catch { return $false }
    finally { $client.Close() }
}

while ($elapsed -lt $maxWait -and -not $allUp) {
    $upList = @()
    foreach ($svc in $services) {
        if (Test-PortFast -Port $svc.port -TimeoutMs 100) {
            $upList += $svc.port
        }
    }
    $upCount = $upList.Count
    $elapsed = [math]::Round(((Get-Date) - $hcStart).TotalSeconds, 1)

    if ($upCount -eq $services.Count) {
        $allUp = $true
        Write-Host ("  All 7 ports OPEN in {0}s" -f $elapsed) -ForegroundColor Green
        break
    }
    Write-Host ("  {0}/{1} open [{2}s]" -f $upCount, $services.Count, $elapsed) -ForegroundColor Gray
    Start-Sleep -Milliseconds 200
}
if (-not $allUp) {
    Write-Host "  Timeout - some services failed" -ForegroundColor Yellow
}

# [3/3] Streamlit
Write-Host "[3/3] Streamlit..." -ForegroundColor Yellow
if (Test-Path $venvUiPython) {
    $uiCmd = "`$Host.UI.RawUI.WindowTitle='Streamlit :8501'; Set-Location '$root'; & '$venvUiPython' -m streamlit run tools\test_console.py; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$uiCmd | Out-Null
    Write-Host "  Streamlit http://localhost:8501" -ForegroundColor Green
}

# [4/4] 감시 창 - 여기서는 --reload 없이 띄웠으므로 되살릴 때도 맞춘다
Write-Host "[4/4] Watchdog..." -ForegroundColor Yellow
$watchdog = Join-Path $root "watchdog.ps1"
if (Test-Path $watchdog) {
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-File",$watchdog,"-NoReload" | Out-Null
    Write-Host "  Watchdog started (-NoReload)" -ForegroundColor Green
}

$total = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)
Write-Host ""
Write-Host ("Total: {0}s" -f $total) -ForegroundColor Cyan
