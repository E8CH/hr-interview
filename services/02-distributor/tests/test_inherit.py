"""승계 배정 — 1단계에서 팀이 적어 낸 '담당팀' 을 그대로 옮긴다.

점수로 다시 나누는 `distribute()` 와 달리 정원·학위비율을 건드리지 않는다.
부서가 이미 나눈 명단이 여기서 바뀌면 부서가 받는 명단과 배정이 또 갈라진다.
"""
import pytest

from app.domain.plan import Move
from app.domain.profile import TeamProfile
from app.services import plan_service
from app.services.distributor_engine import inherit
from tests.test_scorer import make_applicant


def _profile(name: str, **overrides) -> TeamProfile:
    base = dict(
        team_name=name,
        primary_job=["직무다"],
        secondary_job=[],
        preferred_majors=[],
        org_allowed=["제1기술원"],
        grad_ratio_target=0.5,
        target_headcount=1,
    )
    base.update(overrides)
    return TeamProfile(**base)


def profiles() -> list[TeamProfile]:
    return [_profile("AI솔루션팀"), _profile("전극기술팀")]


def person(applicant_id: str, teams: list[str], **overrides):
    return make_applicant(
        applicant_id=applicant_id,
        major_final=None,
        major_bachelor=None,
        assigned_teams=teams,
        **overrides,
    )


def test_single_team_goes_where_it_was_written():
    result = inherit([person("A1", ["전극기술팀"])], profiles())

    assert len(result.assignments) == 1
    row = result.assignments[0]
    assert row.team_name == "전극기술팀"
    assert row.is_duplicate is False
    assert "TEAM_INHERITED" in row.tags
    assert result.team_counts == {"AI솔루션팀": 0, "전극기술팀": 1}
    assert result.total_applicants == 1
    assert result.duplicate_count == 0


def test_two_teams_become_two_seats():
    """두 팀이 같이 적어 냈으면 그대로 두 자리 — 점수 비교로 걸러 내지 않는다."""
    result = inherit([person("A2", ["AI솔루션팀", "전극기술팀"])], profiles())

    assert result.duplicate_count == 1
    primary = [a for a in result.assignments if not a.is_duplicate]
    duplicate = [a for a in result.assignments if a.is_duplicate]
    assert len(primary) == 1 and len(duplicate) == 1
    assert duplicate[0].primary_team == primary[0].team_name
    assert {primary[0].team_name, duplicate[0].team_name} == {"AI솔루션팀", "전극기술팀"}
    assert "DUPLICATE_REVIEW" in duplicate[0].tags
    # 사람은 한 명 — 중복은 자리를 하나 더 쓸 뿐이다
    assert result.total_applicants == 1


def test_capacity_is_not_enforced():
    """정원 1명짜리 팀에 세 명을 적어 냈으면 세 명 다 그 팀이다."""
    people = [person(f"C{i}", ["AI솔루션팀"]) for i in range(3)]
    result = inherit(people, profiles())

    assert result.team_counts["AI솔루션팀"] == 3
    assert result.unassigned == []


def test_org_mismatch_stays_but_is_marked():
    """조직 조건에 안 맞아도 팀이 직접 골랐으면 그 팀에 남는다."""
    outsider = person("A3", ["AI솔루션팀"], team_1st="제2사업부")
    result = inherit([outsider], profiles())

    row = result.assignments[0]
    assert row.team_name == "AI솔루션팀"
    assert row.score == 0.0
    assert "ORG_UNMATCHED" in row.tags


def test_prefilter_still_applies():
    """1차서류·R&D 사전필터는 승계에서도 그대로 — 여기까지가 배포 대상이다."""
    dropped = person("A4", ["AI솔루션팀"], doc_result="결과F")
    kept = person("A5", ["AI솔루션팀"])
    result = inherit([dropped, kept], profiles())

    assert result.filtered_count == 1
    assert [a.applicant_id for a in result.assignments] == ["A5"]


def test_teamless_is_spread_and_unknown_team_is_surfaced():
    """담당팀이 비면 점수로 나눠 담는다 — 모르는 팀 이름은 그래도 이름을 올린다."""
    result = inherit(
        [person("A6", []), person("A7", ["없는팀", "AI솔루션팀"])],
        profiles(),
    )

    assert result.unassigned == []            # 명단에 있는데 아무도 안 보는 사람은 없다
    assert result.unknown_teams == ["없는팀"]
    assert result.auto_filled == 1
    placed = {a.applicant_id: a for a in result.assignments}
    assert placed["A7"].team_name == "AI솔루션팀"
    assert "TEAM_INHERITED" in placed["A7"].tags
    assert "AUTO_FILL" in placed["A6"].tags
    assert "TEAM_INHERITED" not in placed["A6"].tags


def test_auto_fill_spreads_across_teams_instead_of_piling_up():
    """담당팀이 통째로 빈 명단 — 정원을 보고 팀을 고르게 나눠 담는다."""
    people = [person(f"E{i}", []) for i in range(4)]
    result = inherit(people, profiles())     # 팀 둘 · 팀당 정원 1

    assert result.auto_filled == 4
    assert result.unassigned == []
    assert sorted(result.team_counts.values()) == [2, 2]


def test_auto_fill_leaves_room_where_inherit_already_filled():
    """승계로 이미 찬 팀에는 덜 간다 — 남은 정원 위에서 계산하기 때문이다."""
    people = [person("F1", ["AI솔루션팀"]), person("F2", [])]
    result = inherit(people, profiles())     # 팀당 정원 1 — AI솔루션팀은 이미 참

    assert result.team_counts == {"AI솔루션팀": 1, "전극기술팀": 1}


def test_all_rows_have_two_tags():
    result = inherit([person("A8", ["AI솔루션팀", "전극기술팀"])], profiles())
    for assignment in result.assignments:
        assert len(assignment.tags) >= 2


# ------------------------------------------------------------------ 배정안 저장

class _StubClient:
    """01 대신 정해 둔 지원자를 돌려준다."""

    def __init__(self, applicants):
        self._applicants = applicants

    def fetch_master(self, master_version_id: str):
        return list(self._applicants)


def test_create_plan_records_mode_and_duplicate_rows(session):
    summary = plan_service.create_plan(
        session,
        round_id="R2026-Q3-01",
        master_version_id="vm_inherit",
        mode="inherit",
        client=_StubClient([
            person("B1", ["AI솔루션팀", "전극기술팀"]),
            person("B2", ["미래혁신팀"]),
        ]),
    )

    assert summary.mode == "inherit"
    assert summary.total_applicants == 2
    assert summary.duplicate_count == 1
    assert summary.team_counts["미래혁신팀"] == 1

    detail = plan_service.get_plan_detail(session, summary.plan_id)
    assert detail["mode"] == "inherit"
    rows = [row for team_rows in detail["teams"].values() for row in team_rows]
    assert sum(1 for row in rows if row["is_duplicate"]) == 1


def test_create_plan_spreads_when_team_column_is_empty(session):
    """담당팀이 통째로 비어 있어도 막지 않는다 — 승계할 게 없으면 나눠 담는다.

    전체 명단만 올린 회차가 이렇다. 예전에는 400으로 되돌려서 [팀 배정하기] 가
    아예 눌리지 않았다.
    """
    summary = plan_service.create_plan(
        session,
        round_id="R2026-Q3-01",
        master_version_id="vm_bare",
        mode="inherit",
        client=_StubClient([person("B3", []), person("B4", [])]),
    )

    assert summary.mode == "inherit"
    assert summary.auto_filled == 2
    assert summary.unassigned == []
    assert sum(summary.team_counts.values()) == 2


def test_create_plan_defaults_to_auto(session):
    """모드를 안 주면 예전처럼 점수로 새로 나눈다 — 담당팀이 있어도 무시한다."""
    summary = plan_service.create_plan(
        session,
        round_id="R2026-Q3-01",
        master_version_id="vm_auto",
        client=_StubClient([person("B5", ["전극기술팀"])]),
    )
    assert summary.mode == "auto"


# ------------------------------------------------------ 중복 자리는 뒤로도 나간다

def _shared_plan(session, master="vm_seats"):
    return plan_service.create_plan(
        session,
        round_id="R2026-Q3-01",
        master_version_id=master,
        mode="inherit",
        client=_StubClient([
            person("C1", ["AI솔루션팀", "전극기술팀"]),
            person("C2", ["AI솔루션팀"]),
        ]),
    )


def test_plan_applicants_carries_both_seats(session):
    """04(시간표)가 읽는 명단에 같이 보는 자리가 빠지면 한 팀은 면접이 없다."""
    summary = _shared_plan(session)
    rows = plan_service.get_plan_applicants(session, summary.plan_id)

    seats = [(row["applicant_id"], row["team"]) for row in rows]
    assert len(seats) == 3                       # C1 두 자리 + C2 한 자리
    assert seats.count(("C1", "AI솔루션팀")) == 1
    assert seats.count(("C1", "전극기술팀")) == 1

    dup = [row for row in rows if row["applicant_id"] == "C1" and row["is_duplicate"]]
    assert len(dup) == 1
    assert dup[0]["primary_team"] in ("AI솔루션팀", "전극기술팀")
    assert dup[0]["primary_team"] != dup[0]["team"]


def test_adjust_moves_only_the_named_seat(session):
    """같이 보는 사람의 한쪽 팀만 바꾼다 — 나머지 자리는 그대로 남는다."""
    summary = _shared_plan(session, master="vm_seats_move")
    rows = plan_service.get_plan_applicants(session, summary.plan_id)
    dup = next(r for r in rows if r["applicant_id"] == "C1" and r["is_duplicate"])

    plan_service.adjust_plan(
        session,
        summary.plan_id,
        [Move(applicant_id="C1", from_=dup["team"], to="미래혁신팀", reason="검증")],
        actor="pytest",
    )

    after = {
        (row["applicant_id"], row["team"]): row
        for row in plan_service.get_plan_applicants(session, summary.plan_id)
    }
    assert ("C1", "미래혁신팀") in after
    assert ("C1", dup["team"]) not in after
    assert ("C1", dup["primary_team"]) in after      # 주 팀 자리는 그대로
    assert after[("C1", "미래혁신팀")]["is_duplicate"] is True


def test_adjust_refuses_to_put_the_same_person_in_one_team_twice(session):
    """주 팀 자리를 상대 팀으로 옮기면 그 팀이 같은 사람을 두 번 보게 된다."""
    summary = _shared_plan(session, master="vm_seats_clash")
    rows = plan_service.get_plan_applicants(session, summary.plan_id)
    dup = next(r for r in rows if r["applicant_id"] == "C1" and r["is_duplicate"])

    with pytest.raises(plan_service.ServiceError) as exc:
        plan_service.adjust_plan(
            session,
            summary.plan_id,
            [Move(applicant_id="C1", from_=dup["primary_team"], to=dup["team"],
                  reason="검증")],
            actor="pytest",
        )
    assert exc.value.status_code == 409


def test_adjust_keeps_primary_team_pointer_in_step(session):
    """주 팀 자리를 옮기면 같이 보는 자리가 가리키는 이름도 따라간다."""
    summary = _shared_plan(session, master="vm_seats_pointer")
    rows = plan_service.get_plan_applicants(session, summary.plan_id)
    dup = next(r for r in rows if r["applicant_id"] == "C1" and r["is_duplicate"])

    plan_service.adjust_plan(
        session,
        summary.plan_id,
        [Move(applicant_id="C1", from_=dup["primary_team"], to="미래혁신팀",
              reason="검증")],
        actor="pytest",
    )

    after = plan_service.get_plan_applicants(session, summary.plan_id)
    still_dup = next(r for r in after if r["applicant_id"] == "C1" and r["is_duplicate"])
    assert still_dup["primary_team"] == "미래혁신팀"
