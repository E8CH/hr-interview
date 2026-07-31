"""두 팀이 같이 보는 사람 — 자리는 둘, 사람은 하나

02가 같은 지원자를 두 팀 자리로 내려보내면서 생긴 문제들이다. 시간표는 그 사람을
두 번 앉히되 같은 시각에 두 곳으로 보내면 안 되고, 팀마다 부서가 정한 담당자를
따로 지켜야 한다.
"""
from __future__ import annotations

import pytest

from app.domain.schemas import ApplicantIn, GenerateConstraints
from app.services import algorithm_v1, algorithm_v5, registry
from app.services.board import Board
from app.services.constraint_checker import check_hard_constraints

ALL_ALGORITHMS = ["v1", "v4", "v5"]


def _two_teams(interviewers) -> tuple[str, str]:
    teams = sorted({iv.team for iv in interviewers})
    assert len(teams) >= 2, "팀이 둘 이상이어야 중복면접을 만들 수 있다"
    return teams[0], teams[1]


def _shared_seats(interviewers) -> tuple[list[ApplicantIn], str, str]:
    """같은 사람이 두 팀 자리로 들어온 명단 (02의 중복 배정 흉내)."""
    a_team, b_team = _two_teams(interviewers)
    seats = [
        ApplicantIn(applicant_id="DUP1", name="겹친사람", team=a_team, degree="학사"),
        ApplicantIn(applicant_id="DUP1", name="겹친사람", team=b_team, degree="학사"),
    ]
    return seats, a_team, b_team


# ------------------------------------------------------------------ 동시각 금지

def test_board_refuses_same_applicant_at_same_hour(interviewers):
    seats, a_team, b_team = _shared_seats(interviewers)
    board = Board(interviewers)

    assert board.place(seats[0], "월", "09시") is not None
    assert board.can_place(b_team, "월", "09시", "DUP1") is False
    assert board.place(seats[1], "월", "09시") is None
    # 시각만 다르면 두 번째 자리도 잡힌다
    assert board.place(seats[1], "월", "10시") is not None


@pytest.mark.parametrize("algorithm", ALL_ALGORITHMS)
def test_both_seats_are_scheduled_at_different_times(algorithm, interviewers):
    seats, _a, _b = _shared_seats(interviewers)
    plan = registry.run(algorithm, seats, interviewers)

    assert len(plan.assignments) == 2, f"{algorithm}: 자리가 둘인데 한 번만 잡혔다"
    assert {a.team for a in plan.assignments} == {seats[0].team, seats[1].team}
    when = {(a.day, a.hour) for a in plan.assignments}
    assert len(when) == 2, f"{algorithm}: 같은 시각에 두 곳으로 보냈다"
    assert check_hard_constraints(plan.assignments, interviewers) == []


def test_checker_reports_same_applicant_twice_at_one_hour(interviewers):
    """보드를 거치지 않고 만들어진 시간표라도 검증에서는 걸려야 한다."""
    a_team, b_team = _two_teams(interviewers)
    iv_a = next(iv for iv in interviewers if iv.team == a_team)
    iv_b = next(iv for iv in interviewers if iv.team == b_team)
    rows = [
        {"applicant_id": "DUP1", "team": a_team, "interviewer_id": iv_a.interviewer_id,
         "day": "월", "hour": "09시"},
        {"applicant_id": "DUP1", "team": b_team, "interviewer_id": iv_b.interviewer_id,
         "day": "월", "hour": "09시"},
    ]
    codes = [v["code"] for v in check_hard_constraints(rows)]
    assert "APPLICANT_CONFLICT" in codes


# ------------------------------------------------------------------ 팀별 짝

def test_pairs_by_team_keeps_each_team_interviewer(interviewers):
    """팀마다 정한 담당자를 지킨다 — 지원자 번호 하나로 묶으면 한쪽이 덮인다."""
    seats, a_team, b_team = _shared_seats(interviewers)
    iv_a = next(iv for iv in interviewers if iv.team == a_team)
    iv_b = next(iv for iv in interviewers if iv.team == b_team)
    constraints = GenerateConstraints(
        pairs_by_team={
            a_team: {"DUP1": iv_a.interviewer_id},
            b_team: {"DUP1": iv_b.interviewer_id},
        }
    )
    plan = algorithm_v5.run(seats, interviewers, constraints)

    by_team = {a.team: a.interviewer_id for a in plan.assignments}
    assert by_team == {a_team: iv_a.interviewer_id, b_team: iv_b.interviewer_id}


def test_flat_pairs_do_not_seat_someone_with_another_teams_interviewer(interviewers):
    """팀 구분 없는 짝만 있으면 그 담당자의 팀 자리만 잡고 나머지는 비운다."""
    seats, a_team, _b = _shared_seats(interviewers)
    iv_a = next(iv for iv in interviewers if iv.team == a_team)
    plan = algorithm_v1.run(
        seats, interviewers, GenerateConstraints(pairs={"DUP1": iv_a.interviewer_id})
    )

    assert [a.team for a in plan.assignments] == [a_team]
    assert [a.interviewer_id for a in plan.assignments] == [iv_a.interviewer_id]
    assert len(plan.unassigned) == 1
