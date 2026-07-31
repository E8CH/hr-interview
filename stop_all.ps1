chcp 65001 | Out-Null
Write-Host ""
Write-Host "=== HR Interview System - stop ===" -ForegroundColor Cyan

$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

# 감시를 먼저 끈다. 안 그러면 아래에서 내린 서비스를 그대로 다시 살려 버린다.
$pidFile = Join-Path $root ".watchdog.pid"
if (Test-Path $pidFile) {
    $wdPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($wdPid) {
        try {
            Stop-Process -Id $wdPid -Force -ErrorAction Stop
            Write-Host ("  [OK] watchdog (PID {0}) stopped" -f $wdPid) -ForegroundColor Green
        }
        catch {
            Write-Host ("  [--] watchdog (PID {0}) already gone" -f $wdPid) -ForegroundColor Gray
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
else {
    Write-Host "  [--] watchdog not running" -ForegroundColor Gray
}

# 포트가 실제로 비었는지는 붙어 봐야 안다. Get-NetTCPConnection 이 알려 주는
# 주인 PID 는 소켓을 만든 프로세스라서, 그 프로세스가 죽고 자식이 핸들을 물려받아
# 계속 서비스하는 동안에도 죽은 PID 를 그대로 가리킨다.
function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($ar)
        return $true
    }
    catch { return $false }
    finally { $client.Close() }
}

# start_all.ps1 은 --reload 로 띄운다. 이때 uvicorn 은
#   런처(powershell) -> 리로더(python -m uvicorn ... --port N) -> 워커
# 구조가 되는데, 소켓을 만든 쪽은 리로더라서 위 조회는 리로더를 가리킨다.
# 리로더만 죽이면 워커가 고아로 살아남아 계속 포트를 쥔다 — 겉보기엔 껐는데
# 다음에 켤 때 포트가 안 잡히는 이유가 이것이다.
# 그래서 주인과 그 자손을 한꺼번에 죽이고, 포트에 붙어 보며 확인한다.
function Stop-Port([int]$Port) {
    $pattern = "--port\s+$Port\b"

    for ($pass = 1; $pass -le 4; $pass++) {
        if (-not (Test-PortOpen $Port)) { return $true }

        $targets = New-Object 'System.Collections.Generic.HashSet[int]'

        Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match $pattern } |
            ForEach-Object { [void]$targets.Add([int]$_.ProcessId) }

        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { [void]$targets.Add([int]$_.OwningProcess) }

        $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue)
        do {
            $grew = $false
            foreach ($p in $procs) {
                if ($targets.Contains([int]$p.ParentProcessId)) {
                    if ($targets.Add([int]$p.ProcessId)) { $grew = $true }
                }
            }
        } while ($grew)

        foreach ($id in $targets) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 700
    }

    return (-not (Test-PortOpen $Port))
}

$ports = 8001,8002,8003,8004,8005,8006,8007,8501

foreach ($port in $ports) {
    if (-not (Test-PortOpen $port)) {
        Write-Host ("  [--] port {0} not in use" -f $port) -ForegroundColor Gray
        continue
    }
    if (Stop-Port $port) {
        Write-Host ("  [OK] port {0} stopped" -f $port) -ForegroundColor Green
    }
    else {
        Write-Host ("  [FAIL] port {0} still in use" -f $port) -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
