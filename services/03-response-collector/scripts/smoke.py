"""라이브 uvicorn 서버 대상 E2E 스모크 — 완료 판정 체크리스트 실측

    # 터미널 1
    uvicorn app.main:app --port 8003
    # 터미널 2
    python scripts/smoke.py

검증 항목
  1. 폼 페이지 로딩 1초 이내
  2. 응답 제출 500ms 이내
  3. RESPONSE_RECEIVED 이벤트 발행 (/metrics 카운터)
  4. 리마인더 자동 발송 (스케줄러 잡) — sent_at 백데이트 후 폴링 대기
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from shared.contracts.constants import HOURS  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
_results: list[tuple[str, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    _results.append((PASS if condition else FAIL, name, detail))
    mark = "OK  " if condition else "FAIL"
    print(f"  [{mark}] {name}{(' — ' + detail) if detail else ''}")
    return condition


def metric(text: str, key: str) -> float:
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if line.split(" ")[0] == key or line.startswith(key + "{"):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def backdate(db_url: str, request_id: str, hours: float) -> None:
    """스케줄러가 리마인더를 쏘도록 발송 시각을 과거로 이동."""
    import sqlite3

    path = db_url.replace("sqlite:///", "")
    new_sent = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(tzinfo=None)
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE requests SET sent_at = ? WHERE request_id = ?",
        (new_sent.isoformat(sep=" "), request_id),
    )
    conn.commit()
    conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8003")
    parser.add_argument("--db", default="sqlite:///./resp_db.sqlite")
    parser.add_argument(
        "--wait-reminder",
        type=float,
        default=0.0,
        help="스케줄러 자동 발송 대기 시간(초). REMINDER_POLL_SECONDS 를 짧게 두고 실행할 것.",
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    client = httpx.Client(base_url=args.base, timeout=30.0)
    round_id = f"R2026-SMOKE-{int(time.time())}"

    print("=" * 62)
    print(" Service 03 — 완료 판정 체크리스트 실측")
    print("=" * 62)

    # 0) 헬스체크
    health = client.get("/healthz")
    if not check("서비스 기동 (/healthz)", health.status_code == 200, health.text.strip()):
        print("\n서버가 떠 있지 않습니다. `uvicorn app.main:app --port 8003` 먼저 실행하세요.")
        return 1

    before_metrics = client.get("/metrics").text
    before_responses = metric(
        before_metrics, 'respcol_events_published_total{event_type="RESPONSE_RECEIVED"}'
    )

    # 1) 초대 요청 발송
    body = {
        "round_id": round_id,
        "plan_id": f"plan-{round_id}",
        "deadline": (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
        "invitees": [
            {
                "name": f"면접위원{i:02d}",
                "email": f"smoke{i}@lge.com",
                "team": "AI솔루션팀",
                "org": "제1기술원" if i % 2 else "제3기술원",
                "dept_leader_email": "lead@lge.com",
            }
            for i in range(5)
        ],
    }
    created = client.post("/api/v1/requests", json=body)
    check("초대 요청 발송 (201 · REQUEST_SENT)", created.status_code == 201, created.text[:120])
    request_id = created.json()["data"]["request_id"]

    detail = client.get(f"/api/v1/requests/{request_id}").json()["data"]
    check("초대자 5명 등록", detail["invitee_count"] == 5)

    # 폼 토큰은 DB 에서 직접 조회 (공개 API 로는 노출하지 않음)
    import sqlite3

    conn = sqlite3.connect(args.db.replace("sqlite:///", ""))
    tokens = [
        row[0]
        for row in conn.execute(
            "SELECT token FROM invitees WHERE request_id = ?", (request_id,)
        ).fetchall()
    ]
    conn.close()
    check("폼 토큰 발급", len(tokens) == 5 and len(set(tokens)) == 5)

    # 2) 폼 로딩 1초 이내
    client.get(f"/form/{tokens[0]}")  # 워밍업
    samples = []
    for token in tokens:
        start = time.perf_counter()
        page = client.get(f"/form/{token}")
        samples.append(time.perf_counter() - start)
        assert page.status_code == 200
    worst_load = max(samples)
    check("폼 페이지 로딩 1초 이내", worst_load < 1.0, f"최대 {worst_load * 1000:.0f}ms")

    # 3) 응답 제출 500ms 이내
    payload = {
        "job_role": "배터리 소재 연구",
        "available_slots": [
            {"day": "2일차", "hour": HOURS[1]},
            {"day": "2일차", "hour": HOURS[2]},
            {"day": "3일차", "hour": HOURS[3]},
        ],
        "max_daily": 6,
        "backup_contact": "backup@lge.com",
        "notes": "스모크 테스트",
    }
    submit_times = []
    for token in tokens[:3]:
        start = time.perf_counter()
        resp = client.post(f"/form/{token}/submit", json=payload)
        submit_times.append(time.perf_counter() - start)
        assert resp.status_code == 200, resp.text
    worst_submit = max(submit_times)
    check("응답 제출 500ms 이내", worst_submit < 0.5, f"최대 {worst_submit * 1000:.0f}ms")

    # 4) RESPONSE_RECEIVED 이벤트 발행 확인
    after_metrics = client.get("/metrics").text
    after_responses = metric(
        after_metrics, 'respcol_events_published_total{event_type="RESPONSE_RECEIVED"}'
    )
    check(
        "RESPONSE_RECEIVED 이벤트 발행",
        after_responses - before_responses == 3,
        f"{int(after_responses - before_responses)}건 증가",
    )

    # 5) 회신 현황 · 조직 패턴
    summary = client.get(f"/api/v1/responses/{round_id}").json()["data"]
    check(
        "회신 현황 집계",
        summary["total"] == 5 and summary["responded"] == 3 and summary["pending"] == 2,
        f"{summary['responded']}/{summary['total']} 회신",
    )

    patterns = client.get("/api/v1/patterns/organizations").json()["data"]
    check("조직 응답 패턴 학습", len(patterns) >= 1, f"{len(patterns)}개 조직")

    # 6) 리마인더 — 발송 시각을 69h 전으로 백데이트
    backdate(args.db, request_id, 69)

    if args.wait_reminder > 0:
        print(f"\n  스케줄러 자동 발송 대기 중… ({args.wait_reminder:.0f}s)")
        time.sleep(args.wait_reminder)
        reminders = metric(client.get("/metrics").text, "respcol_reminders_total")
        escalations = metric(client.get("/metrics").text, "respcol_escalations_total")
        check(
            "리마인더 자동 발송 (APScheduler)",
            reminders >= 2,
            f"누적 {int(reminders)}건 · 에스컬레이션 {int(escalations)}건",
        )
    else:
        cycle = client.post("/api/v1/reminders/run-cycle").json()["data"]
        check(
            "리마인더 발송 (스케줄러와 동일 코드 경로)",
            cycle["sent_count"] == 2,
            f"{cycle['sent_count']}건 · Level {sorted({s['level'] for s in cycle['sent']})}",
        )
        escalations = metric(client.get("/metrics").text, "respcol_escalations_total")
        check("Level 3 상급자 CC 에스컬레이션", escalations >= 2, f"{int(escalations)}건")

    # 7) 회신자에게는 리마인더가 가지 않았는지
    final = client.get(f"/api/v1/responses/{round_id}").json()["data"]
    responders = [r for r in final["responses"] if r["responded"]]
    check(
        "회신자에겐 리마인더 미발송",
        all(r["last_reminder_level"] == 0 for r in responders),
        f"회신 {len(responders)}명 모두 리마인더 레벨 0",
    )

    # 8) 발송 로그 (mock outbox)
    outbox = Path("./storage/outbox/outbox.jsonl")
    if outbox.exists():
        lines = [json.loads(x) for x in outbox.read_text(encoding="utf-8").splitlines() if x.strip()]
        kinds = {k: sum(1 for m in lines if m["kind"] == k) for k in {"invitation", "reminder"}}
        check("발송 로그 기록", len(lines) > 0, f"초대 {kinds['invitation']}건 · 리마인더 {kinds['reminder']}건")

    print("=" * 62)
    failed = [r for r in _results if r[0] == FAIL]
    print(f" 결과: {len(_results) - len(failed)}/{len(_results)} 통과")
    print("=" * 62)
    client.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
