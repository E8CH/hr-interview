#!/usr/bin/env bash
# 한 컨테이너에서 서비스 7개 + Streamlit 콘솔을 함께 띄운다.
#
# 로컬(start_all.ps1)과 같은 모양을 유지하는 것이 목적이다.
#   - 서비스는 그대로 127.0.0.1:8001~8007 에 뜬다. 콘솔이 쓰는 주소가 바뀌지 않는다.
#   - 서비스마다 자기 디렉터리에서 uvicorn 을 띄운다. app/__init__.py 가
#     parents[3] 로 레포 루트를 잡아 shared/ 를 import 하므로 경로 깊이를 바꾸면 안 된다.
#   - 서비스와 콘솔은 서로 다른 venv 를 쓴다(starlette 버전 충돌). 로컬의 .venv/.venv-ui 와 같다.
#   - DB 와 업로드 파일만 볼륨(/data)으로 뺀다. 나머지 환경변수는 건드리지 않아
#     USE_MOCK·REDIS_URL 등이 로컬 기본값 그대로 동작한다.
set -euo pipefail

APP_ROOT="${APP_ROOT:-/srv}"
DATA_DIR="${DATA_DIR:-/data}"
SVC_PY="${SVC_PY:-/opt/venv-svc/bin/python}"
UI_PY="${UI_PY:-/opt/venv-ui/bin/python}"
PORT="${PORT:-8501}"

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

mkdir -p "$DATA_DIR/db" "$DATA_DIR/storage"

PIDS=()
shutdown() {
  echo "[entrypoint] 종료 신호 — 자식 프로세스 정리"
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  exit 0
}
trap shutdown SIGTERM SIGINT

echo "[entrypoint] 서비스 7개 기동 (data=$DATA_DIR)"
for entry in "${SERVICES[@]}"; do
  IFS=':' read -r name port db <<<"$entry"
  svc_dir="$APP_ROOT/services/$name"
  # 로컬에서 서비스마다 ./storage 를 따로 쓰므로 여기서도 분리해 둔다
  storage="$DATA_DIR/storage/$name"
  mkdir -p "$storage"

  (
    cd "$svc_dir"
    # sqlite:/// + 절대경로 => 슬래시 4개. SQLAlchemy 가 /data/db/... 로 읽는다.
    DATABASE_URL="sqlite:///$DATA_DIR/db/$db" \
    STORAGE_DIR="$storage" \
    exec "$SVC_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$port" --log-level info
  ) &
  PIDS+=($!)
  echo "  [start] $name -> 127.0.0.1:$port"
done

# 포트가 다 열릴 때까지 기다린다. 안 열려도 콘솔은 띄운다 —
# 어느 서비스가 죽었는지는 콘솔의 관리자 화면에서 보는 편이 빠르다.
echo "[entrypoint] 헬스체크 (최대 60초)"
PORTS="8001 8002 8003 8004 8005 8006 8007"
deadline=$((SECONDS + 60))
up=0
while (( SECONDS < deadline )); do
  up=$("$SVC_PY" - "$PORTS" <<'PY'
import socket, sys
n = 0
for p in (int(x) for x in sys.argv[1].split()):
    s = socket.socket()
    s.settimeout(0.3)
    if s.connect_ex(("127.0.0.1", p)) == 0:
        n += 1
    s.close()
print(n)
PY
)
  if (( up == ${#SERVICES[@]} )); then
    echo "  7/7 준비 완료 (${SECONDS}초)"
    break
  fi
  echo "  $up/${#SERVICES[@]} ..."
  sleep 2
done
if (( up != ${#SERVICES[@]} )); then
  echo "  경고: $up/${#SERVICES[@]} 만 떴다. 콘솔은 그대로 띄운다 — 관리자 화면에서 확인할 것."
fi

echo "[entrypoint] Streamlit 콘솔 -> 0.0.0.0:$PORT"
cd "$APP_ROOT"
exec "$UI_PY" -m streamlit run tools/test_console.py \
  --server.port "$PORT" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
