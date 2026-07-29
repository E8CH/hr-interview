"""명세 지정 테스트 — Plan A/B/C 생성"""
from app.services.plan_generator import generate_plans
from app.services.safe_repair import repair_safely


def test_plans_all_three(snapshot, noshow_13):
    """A/B/C 3개 모두 생성됨"""
    plans = generate_plans("EV-ABC", snapshot, noshow_13)
    assert [p.plan_type for p in plans] == ["A_safe", "B_defer", "C_cross_team"]
    for plan in plans:
        assert plan.hard_violations == 0
        assert plan.description
        # 모든 대상자가 각 Plan 에서 빠짐없이 처리된다
        assert {c.applicant_id for c in plan.changes} == set(noshow_13)
        assert plan.rebooked_count + plan.deferred_count == len(noshow_13)


def test_plan_a_prefers_same_team(snapshot):
    """Plan A 는 항상 팀 일치 — cross-team 재예약이 하나도 없어야 한다"""
    noshow = [a.applicant_id for a in snapshot.assignments if a.lock_level != "LOCKED"]
    plans = {p.plan_type: p for p in generate_plans("EV-TEAM", snapshot, noshow)}
    plan_a = plans["A_safe"]

    assert plan_a.cross_team_count == 0
    ap_map = snapshot.applicant_map()
    for change in plan_a.changes:
        if change.action == "rebook":
            assert change.team_match
            assert change.to_slot.team == ap_map[change.applicant_id].team_1st


def test_plan_b_defers_everyone(snapshot, noshow_13):
    plans = {p.plan_type: p for p in generate_plans("EV-B", snapshot, noshow_13)}
    plan_b = plans["B_defer"]
    assert plan_b.rebooked_count == 0
    assert plan_b.deferred_count == len(noshow_13)
    assert all(c.action == "defer" for c in plan_b.changes)


def test_plan_c_allows_cross_team_when_scarce(snapshot):
    """예비 슬롯이 부족한 팀에 노쇼가 몰리면 C 만 재예약을 살려낸다"""
    # 예비 슬롯이 가장 적은 팀(첫 팀)에 노쇼 집중
    scarce_team = snapshot.assignments[0].team
    noshow = [a.applicant_id for a in snapshot.assignments
              if a.team == scarce_team and a.lock_level != "LOCKED"]

    plans = {p.plan_type: p for p in generate_plans("EV-C", snapshot, noshow)}
    plan_a, plan_c = plans["A_safe"], plans["C_cross_team"]

    assert plan_c.rebooked_count > plan_a.rebooked_count
    assert plan_c.cross_team_count > 0
    assert plan_c.warning and "Cross-team" in plan_c.warning
    assert plan_c.hard_violations == 0


def test_plan_c_still_prefers_same_team_first(snapshot, noshow_13):
    """C 도 팀 일치를 먼저 시도한다 — 여유가 있으면 cross-team 0건"""
    plans = {p.plan_type: p for p in generate_plans("EV-C2", snapshot, noshow_13)}
    assert plans["C_cross_team"].cross_team_count == 0
    assert plans["C_cross_team"].rebooked_count == plans["A_safe"].rebooked_count


def test_plan_detail_roundtrip(snapshot, noshow_13):
    """plan_detail JSON 은 DB 저장 후 복원 가능해야 한다"""
    plan = generate_plans("EV-JSON", snapshot, noshow_13)[0]
    detail = plan.to_detail()
    assert len(detail["changes"]) == len(noshow_13)
    assert detail["description"]
    summary = plan.to_summary()
    assert summary["type"] == "A_safe"
    assert summary["hard"] == 0


def test_generate_plans_with_no_reserved_slots(snapshot, noshow_13):
    """예비 슬롯이 전혀 없으면 A/C 도 전원 이월 — 위반은 여전히 0"""
    snapshot.reserved_slots = []
    plans = generate_plans("EV-EMPTY", snapshot, noshow_13)
    assert len(plans) == 3
    for plan in plans:
        assert plan.rebooked_count == 0
        assert plan.deferred_count == len(noshow_13)
        assert plan.hard_violations == 0


def test_repair_is_deterministic(snapshot, noshow_13):
    """같은 입력 → 같은 결과 (HR 이 본 Plan 과 적용 결과가 일치해야 한다)"""
    first = repair_safely(snapshot, noshow_13, allow_cross_team=True)
    second = repair_safely(snapshot, noshow_13, allow_cross_team=True)
    assert [c.model_dump() for c in first.changes] == [c.model_dump() for c in second.changes]
