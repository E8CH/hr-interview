"""API 전 구간 흐름 테스트 — 노쇼 통보 → Plan 조회 → 선택·적용 → 감사 로그"""
import time

from app import events
from app.infrastructure.event_bus import event_bus
from app.services.constraint_recheck import check_hard_constraints
from app.services.scheduler_client import build_mock_schedule

from tests.conftest import ROUND_ID, SCHEDULE_ID


def _noshow_body(applicant_ids, reported_by="HR김민지"):
    return {"round_id": ROUND_ID, "schedule_id": SCHEDULE_ID,
            "noshow_applicant_ids": applicant_ids, "reported_by": reported_by}


def _report(client, applicant_ids, **kw):
    r = client.post("/api/v1/repair/noshow", json=_noshow_body(applicant_ids, **kw))
    assert r.status_code == 202, r.text
    return r.json()["data"]["event_id"]


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_metrics_prometheus_format(client, noshow_13):
    _report(client, noshow_13)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "repair_service_up 1" in r.text
    assert "repair_events_published_total" in r.text


def test_root_envelope(client):
    body = client.get("/").json()
    assert body["error"] is None
    assert body["data"]["service"] == "05-repair-engine"


def test_noshow_returns_202_and_event_id(client, noshow_13):
    r = client.post("/api/v1/repair/noshow", json=_noshow_body(noshow_13))
    assert r.status_code == 202
    body = r.json()
    assert body["error"] is None
    assert body["data"]["status"] == "pending"
    assert body["data"]["plan_count"] == 3


def test_noshow_unknown_applicant_404(client):
    r = client.post("/api/v1/repair/noshow", json=_noshow_body(["9999999"]))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_noshow_empty_list_422(client):
    r = client.post("/api/v1/repair/noshow", json=_noshow_body([]))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_get_plans_matches_spec_shape(client, noshow_13):
    event_id = _report(client, noshow_13)
    body = client.get(f"/api/v1/repair/plans/{event_id}").json()
    plans = body["data"]["plans"]
    assert [p["type"] for p in plans] == ["A_safe", "B_defer", "C_cross_team"]
    for plan in plans:
        assert {"plan_id", "type", "rebooked", "deferred", "hard", "soft",
                "description"} <= set(plan)
        assert plan["hard"] == 0
    assert "cross_team_count" in plans[2]


def test_get_plans_unknown_event_404(client):
    r = client.get("/api/v1/repair/plans/no-such-event")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_select_plan_applies_and_publishes(client, noshow_13):
    event_id = _report(client, noshow_13)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    plan_a = plans[0]

    r = client.post(f"/api/v1/repair/plans/{event_id}/select",
                    json={"plan_id": plan_a["plan_id"], "selected_by": "HR김민지"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["applied"] is True
    assert data["affected_assignments"] == len(noshow_13)
    assert data["hard_violations"] == 0

    executed = event_bus.published(events.REPAIR_EXECUTED)
    assert len(executed) == 1
    payload = executed[0]["payload"]
    assert payload["event_id"] == event_id
    assert payload["plan_type"] == "A_safe"
    assert payload["rebooked"] == data["rebooked"]
    assert executed[0]["producer"] == "repair-engine"
    assert executed[0]["round_id"] == ROUND_ID
    assert executed[0]["correlation_id"]


def test_slot_reopened_published(client, noshow_13):
    event_id = _report(client, noshow_13)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    client.post(f"/api/v1/repair/plans/{event_id}/select",
                json={"plan_id": plans[0]["plan_id"], "selected_by": "HR"})
    reopened = event_bus.published(events.SLOT_REOPENED)
    assert len(reopened) == 1
    assert reopened[0]["payload"]["slot_count"] == len(noshow_13)


def test_participant_deferred_published_for_plan_b(client, noshow_13):
    event_id = _report(client, noshow_13)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    plan_b = next(p for p in plans if p["type"] == "B_defer")

    client.post(f"/api/v1/repair/plans/{event_id}/select",
                json={"plan_id": plan_b["plan_id"], "selected_by": "HR"})
    deferred = event_bus.published(events.PARTICIPANT_DEFERRED)
    assert len(deferred) == 1
    assert sorted(deferred[0]["payload"]["applicant_ids"]) == sorted(noshow_13)


def test_noshow_reported_event_published(client, noshow_13):
    _report(client, noshow_13)
    reported = event_bus.published(events.NOSHOW_REPORTED)
    assert len(reported) == 1
    assert reported[0]["payload"]["reported_by"] == "HR김민지"


def test_double_apply_rejected(client, noshow_13):
    event_id = _report(client, noshow_13)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    plan_id = plans[0]["plan_id"]
    first = client.post(f"/api/v1/repair/plans/{event_id}/select",
                        json={"plan_id": plan_id, "selected_by": "HR"})
    assert first.status_code == 200
    second = client.post(f"/api/v1/repair/plans/{event_id}/select",
                         json={"plan_id": plan_id, "selected_by": "HR"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PLAN_ALREADY_APPLIED"


def test_select_unknown_plan_404(client, noshow_13):
    event_id = _report(client, noshow_13)
    r = client.post(f"/api/v1/repair/plans/{event_id}/select",
                    json={"plan_id": "no-such-plan", "selected_by": "HR"})
    assert r.status_code == 404


def test_applied_schedule_has_zero_hard_violations(client, session, noshow_13):
    from app.services.repair_service import load_snapshot

    event_id = _report(client, noshow_13)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    plan_c = next(p for p in plans if p["type"] == "C_cross_team")
    client.post(f"/api/v1/repair/plans/{event_id}/select",
                json={"plan_id": plan_c["plan_id"], "selected_by": "HR"})

    applied = load_snapshot(session, SCHEDULE_ID, ROUND_ID)
    assert check_hard_constraints(applied.assignments, applied.interviewers) == []


def test_locked_applicants_never_moved_by_api(client, session, noshow_13):
    """완료 판정: LOCKED 배정은 어떤 Plan 에서도 이동하지 않는다"""
    from app.services.repair_service import load_snapshot

    base = build_mock_schedule(SCHEDULE_ID, ROUND_ID)
    locked_before = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
                     for a in base.assignments if a.lock_level == "LOCKED"}
    locked_noshow = [a.applicant_id for a in base.assignments
                     if a.lock_level == "LOCKED"][:4]

    event_id = _report(client, locked_noshow)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    for plan in plans:                       # 세 Plan 모두 LOCKED 는 defer 여야 한다
        for change in plan["changes"]:
            if change["applicant_id"] in locked_noshow:
                assert change["action"] == "defer"

    plan_c = next(p for p in plans if p["type"] == "C_cross_team")
    client.post(f"/api/v1/repair/plans/{event_id}/select",
                json={"plan_id": plan_c["plan_id"], "selected_by": "HR"})

    after = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
             for a in load_snapshot(session, SCHEDULE_ID, ROUND_ID).assignments}
    for applicant_id, slot in locked_before.items():
        if applicant_id in locked_noshow:
            assert applicant_id not in after      # 다음 회차로 이월
        else:
            assert after[applicant_id] == slot    # 그 자리 그대로


def test_audit_log_records_every_repair(client, noshow_13):
    """완료 판정: 감사 로그가 모든 재편성을 기록한다"""
    event_id = _report(client, noshow_13)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    client.post(f"/api/v1/repair/plans/{event_id}/select",
                json={"plan_id": plans[0]["plan_id"], "selected_by": "HR김민지"})

    # 아직 미적용인 두 번째 이벤트도 로그에 남아야 한다
    second_id = _report(client, ["3300002"], reported_by="HR이서준")

    entries = client.get(f"/api/v1/repair/audit/{ROUND_ID}").json()["data"]
    by_id = {e["event_id"]: e for e in entries}
    assert set(by_id) == {event_id, second_id}

    applied = by_id[event_id]
    assert applied["trigger_type"] == "noshow"
    assert applied["selected_plan"] == "A_safe"
    assert applied["selected_by"] == "HR김민지"
    assert applied["applied_at"]
    assert applied["affected_count"] == len(noshow_13)
    assert applied["status"] in ("resolved", "deferred")
    assert applied["plan_count"] == 3

    pending = by_id[second_id]
    assert pending["selected_plan"] is None
    assert pending["status"] == "pending"
    assert pending["reported_by"] == "HR이서준"


def test_audit_log_empty_round(client):
    assert client.get("/api/v1/repair/audit/R-NONE").json()["data"] == []


def test_cancel_applicant_flow(client):
    r = client.post("/api/v1/repair/cancel", json={
        "round_id": ROUND_ID, "schedule_id": SCHEDULE_ID,
        "cancel_type": "applicant", "target_id": "3300002", "reason": "본인 사정"})
    assert r.status_code == 202
    data = r.json()["data"]
    assert data["affected_count"] == 1
    assert data["plan_count"] == 3

    audit = client.get(f"/api/v1/repair/audit/{ROUND_ID}").json()["data"]
    assert audit[0]["trigger_type"] == "cancel_applicant"


def test_cancel_interviewer_reassigns_everyone(client):
    r = client.post("/api/v1/repair/cancel", json={
        "round_id": ROUND_ID, "schedule_id": SCHEDULE_ID,
        "cancel_type": "interviewer", "target_id": "IV001", "reason": "출장"})
    assert r.status_code == 202
    event_id = r.json()["data"]["event_id"]

    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    for plan in plans:
        for change in plan["changes"]:
            if change["to_slot"]:
                assert change["to_slot"]["interviewer_id"] != "IV001"


def test_cancel_unknown_target_404(client):
    r = client.post("/api/v1/repair/cancel", json={
        "round_id": ROUND_ID, "schedule_id": SCHEDULE_ID,
        "cancel_type": "applicant", "target_id": "0000000", "reason": ""})
    assert r.status_code == 404


def test_cancel_invalid_type_422(client):
    r = client.post("/api/v1/repair/cancel", json={
        "round_id": ROUND_ID, "schedule_id": SCHEDULE_ID,
        "cancel_type": "vendor", "target_id": "X", "reason": ""})
    assert r.status_code == 422


def test_locks_endpoints(client, noshow_13):
    _report(client, noshow_13)          # 스냅샷 적재 → lock_map 동기화

    body = client.get(f"/api/v1/repair/locks/{SCHEDULE_ID}").json()["data"]
    assert body["total"] == 65
    assert body["counts"]["LOCKED"] > 0

    targets = [row["applicant_id"] for row in body["locks"]
               if row["lock_level"] == "DRAFT"][:3]
    r = client.post("/api/v1/repair/locks/upgrade", json={
        "schedule_id": SCHEDULE_ID, "applicant_ids": targets, "new_level": "LOCKED"})
    assert r.status_code == 200
    assert r.json()["data"]["upgraded_count"] == 3

    after = client.get(f"/api/v1/repair/locks/{SCHEDULE_ID}").json()["data"]
    upgraded = {row["applicant_id"]: row["lock_level"] for row in after["locks"]}
    assert all(upgraded[t] == "LOCKED" for t in targets)


def test_lock_upgrade_blocks_later_rebooking(client):
    """락 승격 후 통보된 노쇼는 재예약되지 않고 이월된다"""
    base = build_mock_schedule(SCHEDULE_ID, ROUND_ID)
    target = next(a.applicant_id for a in base.assignments if a.lock_level == "DRAFT")

    _report(client, [target])                        # 스냅샷·lock_map 적재
    client.post("/api/v1/repair/locks/upgrade", json={
        "schedule_id": SCHEDULE_ID, "applicant_ids": [target], "new_level": "LOCKED"})

    event_id = _report(client, [target])
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    for plan in plans:
        assert plan["changes"][0]["action"] == "defer"
        assert plan["changes"][0]["reason"] in ("LOCKED_IMMOVABLE", "PLAN_B_DEFER_ALL")


def test_lock_upgrade_invalid_level(client, noshow_13):
    _report(client, noshow_13)
    r = client.post("/api/v1/repair/locks/upgrade", json={
        "schedule_id": SCHEDULE_ID, "applicant_ids": ["3300001"], "new_level": "OPEN"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_end_to_end_13_noshow_under_5s(client, noshow_13):
    """완료 판정: 노쇼 13명 상황에서 통보→Plan 생성→적용까지 5초 이내"""
    started = time.perf_counter()
    event_id = _report(client, noshow_13)
    plans = client.get(f"/api/v1/repair/plans/{event_id}").json()["data"]["plans"]
    applied = client.post(f"/api/v1/repair/plans/{event_id}/select",
                          json={"plan_id": plans[0]["plan_id"], "selected_by": "HR"})
    elapsed = time.perf_counter() - started

    assert applied.status_code == 200
    assert len(plans) == 3
    assert elapsed < 5.0, f"전체 흐름에 {elapsed:.2f}초 소요"
