#!/usr/bin/env bash
# 한 컨테이너에서 서비스 7개 + Streamlit 콘솔을 함께 띄우고, 죽은 것을 다시 살린다.
#
# 로컬(start_all.ps1 + watchdog.ps1)과 같은 모양을 유지하는 것이 목적이다.
#   - 서비스는 그대로 127.0.0.1:8001~8007 에 뜬다. 콘솔이 쓰는 주소가 바뀌지 않는다.
#   - 서비스마다 자기 디렉터리에서 uvicorn 을 띄운다. app/__init__.py 가
#     parents[3] 로 레포 루트를 잡아 shared/ 를 import 하므로 경로 깊이를 바꾸면 안 된다.
#   - 서비스와 콘솔은 서로 다른 venv 를 쓴다(starlette 버전 충돌). 로컬의 .venv/.venv-ui 와 같다.
#   - DB 와 업로드 파일만 볼륨(/data)으로 뺀다. 나머지 환경변수는 건드리지 않아
#     USE_MOCK·REDIS_URL 등이 로컬 기본값 그대로 동작한다.
#
# 감시 방식
#   PID 가 아니라 포트를 본다. 죽은 자식은 거둬들이기 전까지 좀비로 남아
#   `kill -0` 이 계속 성공하므로 PID 로는 죽음을 알 수 없다. 포트가 안 열리면
#   죽었거나 멎은 것으로 보고 그 서비스만 다시 띄운다.
#   콘솔이 내려간 경우는 다시 띄우지 않고 스크립트를 종료한다. railway.json 의
#   restartPolicy(ON_FAILURE)가 컨테이너째 다시 올리는 편이 상태가 깨끗하다.
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv}"
DATA_DIR="${DATA_DIR:-/data}"
SVC_PY="${SVC_PY:-/opt/venv-svc/bin/python}"
UI_PY="${UI_PY:-/opt/venv-ui/bin/python}"
PORT="${PORT:-8501}"
# 감시 주기와, 갓 띄운 서비스에 주는 유예 시간(뜨는 중에 죽었다고 오판하지 않도록)
WATCH_INTERVAL="${WATCH_INTERVAL:-10}"
WATCH_GRACE="${WATCH_GRACE:-25}"

# name:port:db파일 — test_runner.py 의 SERVICES 와 같은 순서·같은 파일명
SERVICES=(
  "01-version-manager:8001:version_db.sqlite"
  "02-distributor:8002:dist_db.sqlite"
  "03-response-collector:8003:resp_db.sqlite"
  "04-scheduler:8004:sched_db.sqlite"
  "05-repair-engine:8005:repair_db.sqlite"
  "06-notification-hub:8006:notif_db.sqlite"
  "07-audit-analytics:8007:audit_db.sqlite"
)

SVC_PORTS=()
for entry in "${SERVICES[@]}"; do
  SVC_PORTS+=("$(cut -d: -f2 <<<"$entry")")
done

mkdir -p "$DATA_DIR/db" "$DATA_DIR/storage"

declare -A PID_OF=() LAST_START=() RESTARTS=()
UI_PID=""

# ── 도구 ──────────────────────────────────────────────────────────────────
# 인자로 준 포트 중 열려 있지 않은 것만 되돌려 준다.
down_ports() {
  "$SVC_PY" - "$@" <<'PY'
import socket, sys
out = []
for p in sys.argv[1:]:
    s = socket.socket()
    s.settimeout(0.5)
    if s.connect_ex(("127.0.0.1", int(p))) != 0:
        out.append(p)
    s.close()
print(" ".join(out))
PY
}

start_service() {
  local name=$1 port=$2 db=$3
  local storage="$DATA_DIR/storage/$name"
  mkdir -p "$storage"
  (
    cd "$APP_ROOT/services/$name"
    # sqlite:/// + 절대경로 => 슬래시 4개. SQLAlchemy 가 /data/db/... 로 읽는다.
    DATABASE_URL="sqlite:///$DATA_DIR/db/$db" \
    STORAGE_DIR="$storage" \
    exec "$SVC_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$port" --log-level info
  ) &
  PID_OF[$name]=$!
  LAST_START[$name]=$SECONDS
}

# sleep 을 그냥 쓰면 SIGTERM 이 sleep 끝날 때까지 밀린다. 배포 교체가 느려지지 않도록
# 백그라운드 sleep 을 wait 로 기다려 신호가 즉시 먹게 한다.
snooze() {
  sleep "$1" &
  wait "$!" 2>/dev/null || true
}

shutdown() {
  echo "[entrypoint] 종료 신호 — 자식 프로세스 정리"
  [ -n "$UI_PID" ] && kill "$UI_PID" 2>/dev/null || true
  for pid in "${PID_OF[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  exit 0
}
trap shutdown SIGTERM SIGINT

# ── 기동 ──────────────────────────────────────────────────────────────────
echo "[entrypoint] 서비스 7개 기동 (data=$DATA_DIR)"
for entry in "${SERVICES[@]}"; do
  IFS=':' read -r name port db <<<"$entry"
  start_service "$name" "$port" "$db"
  echo "  [start] $name -> 127.0.0.1:$port"
done

# 포트가 다 열릴 때까지 기다린다. 안 열려도 콘솔은 띄운다 —
# 못 뜬 서비스는 아래 감시 루프가 계속 다시 시도한다.
echo "[entrypoint] 헬스체크 (최대 60초)"
deadline=$((SECONDS + 60))
while (( SECONDS < deadline )); do
  down=$(down_ports "${SVC_PORTS[@]}" || true)
  if [ -z "$down" ]; then
    echo "  7/7 준비 완료 (${SECONDS}초)"
    break
  fi
  echo "  $(( ${#SERVICES[@]} - $(wc -w <<<"$down") ))/${#SERVICES[@]} ... (대기: $down)"
  sleep 2
done
if [ -n "${down:-}" ]; then
  echo "  경고: 아직 안 뜬 포트가 있다 ($down). 콘솔은 띄우고 감시 루프가 계속 살려 본다."
fi

echo "[entrypoint] Streamlit 콘솔 -> 0.0.0.0:$PORT"
(
  cd "$APP_ROOT"
  exec "$UI_PY" -m streamlit run tools/test_console.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
) &
UI_PID=$!

# ── 감시 ──────────────────────────────────────────────────────────────────
echo "[watchdog] 감시 시작 (${WATCH_INTERVAL}초 주기, 유예 ${WATCH_GRACE}초)"
UI_STARTED=$SECONDS
while true; do
  snooze "$WATCH_INTERVAL"
  down=$(down_ports "${SVC_PORTS[@]}" "$PORT" || true)
  [ -z "$down" ] && continue

  # 콘솔이 내려갔으면 살리지 않고 종료한다 — Railway 가 컨테이너를 다시 올린다.
  if [[ " $down " == *" $PORT "* ]] && (( SECONDS - UI_STARTED >= WATCH_GRACE )); then
    echo "[watchdog] 콘솔(:$PORT)이 응답하지 않는다 — 컨테이너를 내린다. Railway 가 다시 띄운다."
    exit 1
  fi

  for entry in "${SERVICES[@]}"; do
    IFS=':' read -r name port db <<<"$entry"
    [[ " $down " == *" $port "* ]] || continue
    # 갓 띄운 것은 아직 뜨는 중일 수 있다
    (( SECONDS - ${LAST_START[$name]} >= WATCH_GRACE )) || continue

    old=${PID_OF[$name]:-}
    if [ -n "$old" ]; then
      kill -9 "$old" 2>/dev/null || true
      # 좀비로 남지 않도록 거둬들인다
      wait "$old" 2>/dev/null || true
    fi
    RESTARTS[$name]=$(( ${RESTARTS[$name]:-0} + 1 ))
    echo "[watchdog] $name (:$port) 응답 없음 — 재시작 #${RESTARTS[$name]}"
    start_service "$name" "$port" "$db"
  done
done
