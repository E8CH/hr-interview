"""완료 판정 체크리스트 자동 검증 (bmad/05_repair_engine.md)

실행: python verify_checklist.py
서버 기동 없이 in-process TestClient 로 전 구간을 실행한다.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVICE_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="repair-verify-")) / "verify.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.as_posix()}"
os.environ["USE_MOCK"] = "true"
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["LOG_LEVEL"] = "ERROR"

from fastapi.testclient import TestClient          # noqa: E402

from app import events                             # noqa: E402
from app.infrastructure.db import SessionLocal     # noqa: E402
from app.infrastructure.event_bus import event_bus  # noqa: E402
from app.main import app                           # noqa: E402
from app.services.constraint_recheck import check_hard_constraints  # noqa: E402
from app.services.repair_service import load_snapshot  # noqa: E402
from app.services.scheduler_client import build_mock_schedule  # noqa: E402

ROUND_ID = "R2026-Q3-01"
SCHEDULE_ID = "SCH-VERIFY"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, title: str, detail: str = "") -> None:
    results.append((ok, title, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {title}" + (f" — {detail}" if detail else ""))


def main() -> int:
    base = build_mock_schedule(SCHEDULE_ID, ROUND_ID)
    noshow = [a.applicant_id for i, a in enumerate(base.assignments) if i % 5 == 1][:13]
    locked_before = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
                     for a in base.assignments if a.lock_level == "LOCKED"}

    print(f"\n시나리오: {len(base.assignments)}건 배정 · 예비 슬롯 {len(base.reserved_slots)}개 "
          f"· 노쇼 {len(noshow)}명 (LOCKED {len(locked_before)}건)\n")

    with TestClient(app) as client:
        started = time.perf_counter()
        r = client.post("/api/v1/repair/noshow", json={
            "round_id": ROUND_ID, "schedule_id": SCHEDULE_ID,
            "noshow_applicant_ids": noshow, "reported_by": "HR김민지"})
        event_id = r.json()["data"]["event_id"]
        plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
        plan_a = plans[0]
        applied = client.post(f"/api/v1/repair/plans/{event_id}/select",
                              json={"plan_id": plan_a["plan_id"],
                                    "selected_by": "HR김민지"}).json()["data"]
        elapsed = time.perf_counter() - started

        # 1. 노쇼 13명 재편성 5초 이내
        check(elapsed < 5.0, "노쇼 13명 재편성 5초 이내", f"{elapsed:.3f}초")

        # 2. Plan A/B/C 자동 생성
        types = [p["type"] for p in plans]
        check(types == ["A_safe", "B_defer", "C_cross_team"],
              "Plan A/B/C 자동 생성", " · ".join(
                  f"{p['type']}(재예약 {p['rebooked']}/이월 {p['deferred']}/soft {p['soft']})"
                  for p in plans))

        # 3. LOCKED 배정은 어떤 Plan 에서도 이동 안 됨
        locked_moved = [c["applicant_id"] for p in plans for c in p["changes"]
                        if c["applicant_id"] in locked_before and c["action"] != "defer"]
        session = SessionLocal()
        try:
            final = load_snapshot(session, SCHEDULE_ID, ROUND_ID)
        finally:
            session.close()
        after = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
                 for a in final.assignments}
        untouched = all(after.get(aid) == slot for aid, slot in locked_before.items()
                        if aid not in noshow)
        check(not locked_moved and untouched,
              "LOCKED 배정은 어떤 Plan 에서도 이동 안 됨",
              f"LOCKED {len(locked_before)}건 전부 원위치 유지")

        # 하드 위반 0 (성공 기준)
        violations = check_hard_constraints(final.assignments, final.interviewers)
        check(applied["hard_violations"] == 0 and not violations,
              "적용 후 하드 제약 위반 0건",
              f"재예약 {applied['rebooked']} · 이월 {applied['deferred']}")

        # 4. REPAIR_EXECUTED 이벤트 발행
        executed = event_bus.published(events.REPAIR_EXECUTED)
        check(len(executed) == 1 and executed[0]["payload"]["event_id"] == event_id,
              "REPAIR_EXECUTED 이벤트 발행",
              f"발행 이벤트: {[e['event_type'] for e in event_bus.published()]}")

        # 5. 감사 로그가 모든 재편성 기록
        audit = client.get(f"/api/v1/repair/audit/{ROUND_ID}").json()["data"]
        entry = audit[0] if audit else {}
        check(len(audit) == 1
              and entry.get("selected_plan") == "A_safe"
              and entry.get("selected_by") == "HR김민지"
              and entry.get("applied_at") is not None
              and entry.get("affected_count") == len(noshow),
              "감사 로그가 모든 재편성 기록",
              f"{entry.get('trigger_type')} → {entry.get('selected_plan')} "
              f"by {entry.get('selected_by')} ({entry.get('affected_count')}건)")

    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n결과: {passed}/{len(results)} 통과")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
