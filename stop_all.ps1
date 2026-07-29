chcp 65001 | Out-Null
Write-Host ""
Write-Host "=== HR Interview System - stop ===" -ForegroundColor Cyan

$ports = 8001,8002,8003,8004,8005,8006,8007,8501

foreach ($port in $ports) {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) {
        try {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop
            Stop-Process -Id $conn.OwningProcess -Force
            Write-Host ("  [OK] port {0} ({1}) stopped" -f $port, $proc.ProcessName) -ForegroundColor Green
        }
        catch {
            Write-Host ("  [FAIL] port {0}" -f $port) -ForegroundColor Yellow
        }
    }
    else {
        Write-Host ("  [--] port {0} not in use" -f $port) -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
