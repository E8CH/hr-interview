"""
HR Interview System - CLI Test Runner (v2, /api/v1 반영)
Usage:
  python tools/test_runner.py health
  python tools/test_runner.py scenario happy
  python tools/test_runner.py events R2026-TEST-01
  python tools/test_runner.py db
  python tools/test_runner.py menu
"""
import sys, time, sqlite3
from pathlib import Path

try:
    import httpx
except ImportError:
    print("❌ httpx 미설치: pip install httpx")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MASTER = ROOT / "docs" / "취합파일.xlsx"
FALLBACK_MASTER = ROOT / "tools" / "fixtures" / "master_sample.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SERVICES = [
    ("version-manager", 8001, "version_db.sqlite"),
    ("distributor", 8002, "dist_db.sqlite"),
    ("response-collector", 8003, "resp_db.sqlite"),
    ("scheduler", 8004, "sched_db.sqlite"),
    ("repair-engine", 8005, "repair_db.sqlite"),
    ("notification-hub", 8006, "notif_db.sqlite"),
    ("audit-analytics", 8007, "audit_db.sqlite"),
]

def unwrap(response):
    """공통 응답 봉투 {"data":..., "error":...} 에서 data 를 꺼낸다."""
    try:
        body = response.json()
    except Exception:
        return None
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def error_text(response):
    data = None
    try:
        body = response.json()
        data = body.get("error") if isinstance(body, dict) else None
    except Exception:
        pass
    return str(data or response.text)[:300]


def find_master():
    """등록에 쓸 마스터 엑셀 경로. 없으면 None."""
    for path in (DEFAULT_MASTER, FALLBACK_MASTER):
        if path.is_file():
            return path
    return None


def cmd_health():
    print("\n=== 서비스 헬스체크 ===")
    ok = 0
    for name, port, _ in SERVICES:
        try:
            r = httpx.get(f"http://localhost:{port}/healthz", timeout=1.0)
            if r.status_code == 200:
                print(f"  ✅ OK    port {port}  {name}")
                ok += 1
            else:
                print(f"  ⚠️  {r.status_code}   port {port}  {name}")
        except Exception as e:
            print(f"  ❌ DOWN  port {port}  {name}  ({type(e).__name__})")
    print(f"\n{'🎉 7개 서비스 모두 정상' if ok == len(SERVICES) else f'⚠️  {ok}/{len(SERVICES)} 정상 (일부 서비스 미기동)'}")
    return ok == len(SERVICES)

def cmd_scenario(kind="happy"):
    round_id = f"R2026-TEST-{int(time.time()) % 10000:04d}"
    print(f"\n=== 시나리오: {kind} (Round {round_id}) ===\n")

    version_id = None
    plan_id = None
    schedule_id = None

    # Step 1: version register — multipart/form-data (file + round_id + kind + actor)
    print(f"[1/4] Version Manager - Round {round_id} 등록")
    master = find_master()
    if master is None:
        print(f"  ⏭  마스터 엑셀 없음 ({DEFAULT_MASTER}) — 이후 단계 skip")
    else:
        try:
            r = httpx.post(
                "http://localhost:8001/api/v1/versions/register",
                files={"file": (master.name, master.read_bytes(), XLSX_MIME)},
                data={"round_id": round_id, "kind": "master", "actor": "test_runner"},
                timeout=30.0,
            )
            print(f"  → status {r.status_code}")
            if r.status_code < 300:
                data = unwrap(r) or {}
                version_id = data.get("version_id")
                print(f"  version_id: {version_id}  applicants: {data.get('applicant_count')}")
            else:
                print(f"  ❌ {error_text(r)}")
        except Exception as e:
            print(f"  ❌ {e}")

    # Step 2: distribute plan — master_version_id 필수
    print(f"[2/4] Distributor - 팀 배포 계획 생성")
    if not version_id:
        print("  ⏭  version_id 없음 — skip")
    else:
        try:
            r = httpx.post(
                "http://localhost:8002/api/v1/distribute/plan",
                json={
                    "round_id": round_id,
                    "master_version_id": version_id,
                    "created_by": "test_runner",
                },
                timeout=60.0,
            )
            print(f"  → status {r.status_code}")
            if r.status_code < 300:
                data = unwrap(r) or {}
                plan_id = data.get("plan_id")
                print(f"  plan_id: {plan_id}  applicants: {data.get('total_applicants')}")
            else:
                print(f"  ❌ {error_text(r)}")
        except Exception as e:
            print(f"  ❌ {e}")

    # Step 3: schedule generate — plan_id 필수(null 이면 400)
    print(f"[3/4] Scheduler - 일정 생성")
    if not plan_id:
        print("  ⏭  plan_id 없음 — skip")
    else:
        try:
            r = httpx.post(
                "http://localhost:8004/api/v1/schedules/generate",
                json={
                    "round_id": round_id,
                    "plan_id": plan_id,
                    "algorithm": "v5",
                    "generated_by": "test_runner",
                },
                timeout=60.0,
            )
            print(f"  → status {r.status_code}")
            if r.status_code < 300:
                data = unwrap(r) or {}
                schedule_id = data.get("schedule_id")
                print(
                    f"  schedule_id: {schedule_id}  "
                    f"assigned: {data.get('total_assigned')}  "
                    f"hard_violations: {data.get('hard_violations')}"
                )
            else:
                print(f"  ❌ {error_text(r)}")
        except Exception as e:
            print(f"  ❌ {e}")

    # Step 4: audit — 07 로의 이벤트 포워딩은 비동기라 잠깐 재시도한다
    print(f"[4/4] Audit - 이벤트 타임라인 확인")
    try:
        events = []
        for _ in range(10):
            r = httpx.get(
                "http://localhost:8007/api/v1/audit/timeline",
                params={"round_id": round_id}, timeout=5.0
            )
            data = unwrap(r)
            events = data if isinstance(data, list) else (data or {}).get("events", [])
            if events:
                break
            time.sleep(0.3)
        print(f"  → status {r.status_code}, events: {len(events)}")
        for ev in events:
            print(f"     · {ev.get('event_type')}  ({ev.get('producer')})")
    except Exception as e:
        print(f"  ❌ {e}")

    print(f"\n✅ 완료 (round_id={round_id})")

def cmd_events(round_id):
    print(f"\n=== 이벤트 타임라인: {round_id} ===")
    try:
        r = httpx.get(
            "http://localhost:8007/api/v1/audit/timeline",
            params={"round_id": round_id}, timeout=5.0
        )
        print(f"status {r.status_code}")
        if r.status_code == 200:
            data = unwrap(r)
            events = data if isinstance(data, list) else (data or {}).get("events", [])
            for i, ev in enumerate(events, 1):
                print(f"  [{i}] {ev}")
            print(f"\n총 {len(events)}건")
        else:
            print(r.text[:300])
    except Exception as e:
        print(f"❌ {e}")

def cmd_db():
    print("\n=== 각 서비스 DB 상태 ===")
    for name, port, dbname in SERVICES:
        svc_dir = f"0{port-8000}-" + name if port != 8001 else "01-version-manager"
        # 폴더명 매핑
        folder_map = {
            8001: "01-version-manager", 8002: "02-distributor",
            8003: "03-response-collector", 8004: "04-scheduler",
            8005: "05-repair-engine", 8006: "06-notification-hub",
            8007: "07-audit-analytics",
        }
        db_path = ROOT / "services" / folder_map[port] / dbname
        if not db_path.exists():
            print(f"  ❌ {name}  (DB 없음)")
            continue
        size_kb = db_path.stat().st_size / 1024
        print(f"\n  📦 {name}  ({size_kb:.1f} KB)")
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [r[0] for r in cur.fetchall() if not r[0].startswith("sqlite_")]
            for t in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    cnt = cur.fetchone()[0]
                    print(f"     · {t:<35} {cnt}행")
                except Exception:
                    print(f"     · {t:<35} ?")
            conn.close()
        except Exception as e:
            print(f"     조회 실패: {e}")

def cmd_menu():
    while True:
        print("\n=== HR Interview Test Menu ===")
        print("  1) health")
        print("  2) scenario happy")
        print("  3) db")
        print("  4) events (round_id 입력)")
        print("  0) exit")
        ch = input("> ").strip()
        if ch == "1": cmd_health()
        elif ch == "2": cmd_scenario("happy")
        elif ch == "3": cmd_db()
        elif ch == "4":
            rid = input("round_id: ").strip()
            cmd_events(rid)
        elif ch == "0": break

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "menu":
        cmd_menu()
    elif args[0] == "health":
        sys.exit(0 if cmd_health() else 1)
    elif args[0] == "scenario":
        cmd_scenario(args[1] if len(args) > 1 else "happy")
    elif args[0] == "events":
        if len(args) < 2:
            print("Usage: test_runner.py events <round_id>")
            sys.exit(1)
        cmd_events(args[1])
    elif args[0] == "db":
        cmd_db()
    else:
        print(f"Unknown: {args[0]}")
        print("Commands: health | scenario happy | events <id> | db | menu")
