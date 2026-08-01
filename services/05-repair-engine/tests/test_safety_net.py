"""방어 로직 테스트 — 증분 검증이 깨져도 하드 위반 0 이 보장되는가"""
import httpx
import pytest

from app.config import settings
from app.services import safe_repair, scheduler_client
from app.services.constraint_recheck import ConstraintIndex, check_hard_constraints
from app.services.plan_generator import generate_plans


def test_rollback_when_incremental_check_is_broken(snapshot, monkeypatch, noshow_13):
    """증분 인덱스가 모든 슬롯을 '안전'이라 잘못 답해도 전수 검사가 막아낸다.

    v3 의 실패 모드(재검증 누락)를 인위적으로 재현한 회귀 테스트.
    """
    monkeypatch.setattr(ConstraintIndex, "is_safe",
                        lambda self, *args, **kwargs: True)

    outcome = safe_repair.repair_safely(snapshot, noshow_13, allow_cross_team=True)

    assert outcome.hard_violations == 0, outcome.violation_details
    assert check_hard_constraints(outcome.assignments, snapshot.interviewers) == []
    # 되돌려진 재예약은 이월로 전환되어 있어야 한다
    rolled_back = [c for c in outcome.changes if c.reason == "ROLLED_BACK_UNSAFE"]
    for change in rolled_back:
        assert change.action == "defer"
        assert change.to_slot is None
    # 대상자는 여전히 빠짐없이 처리된다
    assert {c.applicant_id for c in outcome.changes} == set(noshow_13)


def test_plan_discarded_if_input_schedule_already_violating(snapshot, noshow_13):
    """입력 시간표에 이미 위반이 있으면 안전한 Plan 을 제시하지 않는다"""
    clone = snapshot.assignments[0].model_copy(update={
        "assignment_id": "DUP-1", "applicant_id": "9999998"})
    snapshot.assignments.append(clone)
    snapshot.applicants.append(snapshot.applicants[0].model_copy(update={
        "applicant_id": "9999998"}))

    assert check_hard_constraints(snapshot.assignments, snapshot.interviewers)
    assert generate_plans("EV-DIRTY", snapshot, noshow_13) == []


def test_untouched_invariant_raises_on_tampering(snapshot, monkeypatch, noshow_13):
    """대상 외 배정이 바뀌면 조용히 넘어가지 않고 즉시 실패한다"""
    def tampering_add(self, assignment):
        assignment.day = "5일차"          # 무관한 배정을 훼손하는 상황을 흉내

    original = snapshot.assignments[0]
    with pytest.raises(AssertionError):
        safe_repair._assert_untouched(
            [original],
            [original.model_copy(update={"day": "5일차", "hour": "16시"})])


def test_assert_untouched_detects_dropped_assignment(snapshot):
    original = snapshot.assignments[0]
    with pytest.raises(AssertionError):
        safe_repair._assert_untouched([original], [])


def test_remote_scheduler_fetch(monkeypatch, snapshot):
    """USE_MOCK=false 이면 Service 04 REST API 로 시간표를 로드한다"""
    payload = snapshot.model_dump(mode="json")

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": payload, "error": None}

    calls = {}

    def fake_get(url, **kwargs):
        calls["url"] = url
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(settings, "use_mock", False)

    loaded = scheduler_client.fetch_schedule(snapshot.schedule_id, snapshot.round_id)
    assert loaded.schedule_id == snapshot.schedule_id
    assert len(loaded.assignments) == len(snapshot.assignments)
    assert calls["url"].endswith(f"/api/v1/schedule/{snapshot.schedule_id}")


def test_mock_schedule_is_deterministic():
    first = scheduler_client.build_mock_schedule("S1", "R1")
    second = scheduler_client.build_mock_schedule("S1", "R1")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_snapshot_helpers(snapshot):
    assert snapshot.assignment_of("no-such-applicant") is None
    first = snapshot.assignments[0]
    assert snapshot.assignment_of(first.applicant_id) is first
    assert len(snapshot.interviewer_map()) == len(snapshot.interviewers)
    assert len(snapshot.applicant_map()) == len(snapshot.applicants)
