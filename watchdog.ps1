# 서비스가 죽으면 그 서비스 창만 다시 띄운다.
#
# 컨테이너의 docker/start-all.sh 감시 루프와 같은 일을 로컬에서 한다.
#   - 서비스 목록은 services\NN-이름 디렉터리에서 직접 읽는다. 포트는 8000+NN.
#     (test_runner.py 가 쓰는 것과 같은 규칙이라, 서비스를 늘려도 손댈 필요가 없다)
#   - 포트만 보지 않고 /healthz 를 부른다. 포트는 열려 있는데 멎은 경우까지 잡는다.
#   - 되살리기 전에 그 포트를 쓰던 uvicorn 을 먼저 정리한다 (Stop-Port 주석 참고).
#
# --reload 로 띄운 경우 워커가 죽으면 uvicorn 리로더가 알아서 새로 띄운다.
# 그래서 이 감시가 실제로 나서는 때는 리로더까지 죽었을 때다 — 창을 닫았거나,
# 기동 자체가 실패했거나, 포트는 열려 있는데 앱이 멎은 경우.
#
# 죽은 창은 'Press Enter to close' 상태로 남겨 둔다. 왜 죽었는지 봐야 하기 때문이다.
#
# 중지: .\stop_all.ps1 (감시를 먼저 끄고 서비스를 내린다)
#       또는 이 창에서 Ctrl+C
param(
    [int]$IntervalSec = 10,
    # 갓 띄운 서비스는 뜨는 중일 수 있다. 이 시간 안에는 죽었다고 보지 않는다.
    [int]$GraceSec = 25,
    # start_all_fast.ps1 / start_all_ultra.ps1 처럼 --reload 없이 띄웠을 때
    [switch]$NoReload,
    [switch]$SkipConsole
)

chcp 65001 | Out-Null
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

$venvPython   = Join-Path $root ".venv\Scripts\python.exe"
$venvUiPython = Join-Path $root ".venv-ui\Scripts\python.exe"
$pidFile      = Join-Path $root ".watchdog.pid"
$consolePort  = 8501

if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: .venv\Scripts\python.exe not found" -ForegroundColor Red
    exit 1
}

# 두 개가 동시에 돌면 서로 되살리기를 반복한다
if (Test-Path $pidFile) {
    $oldPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($oldPid) {
        $running = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($running) {
            Write-Host ("이미 감시가 돌고 있다 (PID {0}). 중복 실행하지 않는다." -f $oldPid) -ForegroundColor Yellow
            exit 0
        }
    }
}

# ── 서비스 목록 ───────────────────────────────────────────────────────────
$services = @()
Get-ChildItem -Path (Join-Path $root "services") -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name |
    ForEach-Object {
        if ((Test-Path (Join-Path $_.FullName "app\main.py")) -and ($_.Name -match '^(\d+)-')) {
            $services += [pscustomobject]@{
                Name = $_.Name
                Port = 8000 + [int]$Matches[1]
                Path = $_.FullName
            }
        }
    }

if ($services.Count -eq 0) {
    Write-Host "ERROR: services\ 아래에서 서비스를 찾지 못했다" -ForegroundColor Red
    exit 1
}

# ── 도구 ──────────────────────────────────────────────────────────────────
function Test-Healthy([int]$Port, [string]$Path) {
    try {
        $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}{1}" -f $Port, $Path) `
                               -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    }
    catch { return $false }
}

# 포트가 실제로 비었는지는 붙어 봐야 안다.
# Get-NetTCPConnection 의 주인 PID 는 소켓을 "만든" 프로세스라서, 그 프로세스가
# 죽고 자식이 핸들을 물려받아 계속 서비스하는 동안에도 죽은 PID 를 그대로 가리킨다.
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

# 되살리기 전에 그 포트를 쓰던 프로세스를 확실히 정리한다.
#
# 윈도에서 uvicorn --reload 는 프로세스가 셋이다.
#   런처(powershell) -> 리로더(python -m uvicorn ... --port N) -> 워커
# 소켓을 쥔 쪽은 맨 아래 워커인데, 이 워커의 명령줄은
#   python -c "from multiprocessing.spawn import spawn_main; ..."
# 이라서 'uvicorn' 도 '--port N' 도 들어 있지 않다. 명령줄로는 절대 못 찾는다.
# 게다가 리로더가 먼저 죽으면 워커는 고아로 살아남아 계속 200 을 돌려준다.
#
# 그래서 세 갈래로 모아서 한꺼번에 죽인다.
#   1) 명령줄에 uvicorn + --port N 이 있는 것 (리로더)
#   2) 그 포트의 소켓 주인 전부 (죽은 PID 일 수도 있다)
#   3) 1·2 의 자손 전부 — 여기에 워커가 있다. 부모가 이미 죽었어도 걸린다.
# 한 번에 안 되면 포트에 직접 붙어 보며 몇 번 더 시도한다.
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

        # 자손으로 넓힌다. 부모가 목록에 들어오면 그 자식도 따라 들어오므로 반복한다.
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

    if (Test-PortOpen $Port) {
        Write-Host ("  경고: :{0} 를 비우지 못했다. 그대로 다시 띄운다." -f $Port) -ForegroundColor Red
        return $false
    }
    return $true
}

function Start-ServiceWindow($svc) {
    $title = "{0} :{1}" -f $svc.Name, $svc.Port
    $reload = " --reload"
    if ($NoReload) { $reload = "" }
    $inner = "`$Host.UI.RawUI.WindowTitle='$title'; chcp 65001 | Out-Null; " +
             "Set-Location '$($svc.Path)'; " +
             "Write-Host '=== $($svc.Name) port $($svc.Port) [watchdog 재시작] ===' -ForegroundColor Cyan; " +
             "& '$venvPython' -m uvicorn app.main:app --port $($svc.Port)$reload; " +
             "Write-Host 'Press Enter to close'; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$inner | Out-Null
}

function Start-ConsoleWindow {
    $inner = "`$Host.UI.RawUI.WindowTitle='Streamlit :$consolePort'; chcp 65001 | Out-Null; " +
             "Set-Location '$root'; " +
             "& '$venvUiPython' -m streamlit run tools\test_console.py; Read-Host"
    Start-Process powershell -ArgumentList "-NoExit","-ExecutionPolicy","Bypass","-Command",$inner | Out-Null
}

# ── 시작 ──────────────────────────────────────────────────────────────────
Set-Content -Path $pidFile -Value $PID -Encoding utf8

$Host.UI.RawUI.WindowTitle = "Watchdog"
Write-Host ""
Write-Host "=== HR Interview System - watchdog ===" -ForegroundColor Cyan
Write-Host ("  감시 대상: {0}개 서비스 ({1})" -f $services.Count,
            (($services | ForEach-Object { $_.Port }) -join ",")) -ForegroundColor Gray
if (-not $SkipConsole) {
    Write-Host ("  콘솔 :{0} 도 함께 본다" -f $consolePort) -ForegroundColor Gray
}
Write-Host ("  주기 {0}초 / 유예 {1}초 / PID {2}" -f $IntervalSec, $GraceSec, $PID) -ForegroundColor Gray
Write-Host "  중지: .\stop_all.ps1 또는 Ctrl+C" -ForegroundColor Gray
Write-Host ""

# 지금 떠 있는 것들은 방금 뜬 것으로 보고 유예를 준다
$lastStart = @{}
$restarts  = @{}
$now = Get-Date
foreach ($svc in $services) { $lastStart[$svc.Port] = $now }
$lastStart[$consolePort] = $now

try {
    while ($true) {
        Start-Sleep -Seconds $IntervalSec

        foreach ($svc in $services) {
            if (Test-Healthy $svc.Port "/healthz") { continue }
            if (((Get-Date) - $lastStart[$svc.Port]).TotalSeconds -lt $GraceSec) { continue }

            if (-not $restarts.ContainsKey($svc.Port)) { $restarts[$svc.Port] = 0 }
            $restarts[$svc.Port]++
            Write-Host ("[{0}] {1} (:{2}) 응답 없음 - 재시작 #{3}" -f `
                        (Get-Date -Format "HH:mm:ss"), $svc.Name, $svc.Port, $restarts[$svc.Port]) `
                       -ForegroundColor Yellow
            Stop-Port $svc.Port
            Start-ServiceWindow $svc
            $lastStart[$svc.Port] = Get-Date
        }

        if (-not $SkipConsole) {
            if (-not (Test-Path $venvUiPython)) { continue }
            if (Test-Healthy $consolePort "/_stcore/health") { continue }
            if (((Get-Date) - $lastStart[$consolePort]).TotalSeconds -lt $GraceSec) { continue }

            if (-not $restarts.ContainsKey($consolePort)) { $restarts[$consolePort] = 0 }
            $restarts[$consolePort]++
            Write-Host ("[{0}] 콘솔 (:{1}) 응답 없음 - 재시작 #{2}" -f `
                        (Get-Date -Format "HH:mm:ss"), $consolePort, $restarts[$consolePort]) `
                       -ForegroundColor Yellow
            Stop-Port $consolePort
            Start-ConsoleWindow
            $lastStart[$consolePort] = Get-Date
        }
    }
}
finally {
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "감시 종료." -ForegroundColor Green
}
