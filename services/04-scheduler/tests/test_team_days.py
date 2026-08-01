"""팀별 면접 요일은 인사가 명단을 보낼 때 한 번 정하고 끝이다

예전에는 시간표를 만들 때마다 요일을 다시 뽑았다. 부서에서 사람을 덜 고르면
팀 인원이 줄고, 인원이 줄면 요일 순서가 바뀌어, 같은 명단으로 두 번 만들면
서로 다른 요일의 시간표가 나왔다. 부서가 '1일차' 에 잡아 둔 자리는 그때마다
다른 요일을 가리키게 된다.

그래서 요일은 3단계(명단 보내기)에서 못 박고, 그 뒤 단계는 받아 쓰기만 한다.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn
from app.infrastructure.contracts import (DAYS, DAYS_PER_TEAM, HOURS, band_hours,
                                          plan_team_days)
from app.services import algorithm_v4, hierarchical

BAND = "둘 다"


def _crew(team: str, count: int = 2) -> list[InterviewerIn]:
    return [
        InterviewerIn(
            interviewer_id=f"{team}-IV{i}", name=f"{team}-IV{i}", team=team,
            max_daily=len(HOURS), priority=2,
            availability={day: list(band_hours(BAND)) for day in DAYS},
        )
        for i in range(count)
    ]


def _people(team: str, count: int) -> list[ApplicantIn]:
    return [
        ApplicantIn(applicant_id=f"{team}-A{i:02d}", name=f"{team}{i:02d}",
                    team=team, degree="학사")
        for i in range(count)
    ]


# ------------------------------------------------------------------ 같은 값인가

def test_same_sizes_give_the_same_days():
    """같은 명단이면 몇 번을 돌려도 같은 요일이 나온다."""
    sizes = {"가팀": 12, "나팀": 9, "다팀": 7}

    first = plan_team_days(sizes)
    again = plan_team_days(sizes)

    assert first == again
    assert all(len(days) == 3 for days in first.values())
    assert all(day in DAYS for days in first.values() for day in days)


def test_days_come_out_in_week_order():
    """요일은 월 → 금 차례로 적힌다 — 화면이 '1일차' 를 세는 순서다."""
    for days in plan_team_days({"가팀": 20, "나팀": 4}).values():
        assert days == sorted(days, key=DAYS.index)


def test_bigger_teams_get_the_emptier_days():
    """큰 팀부터 한산한 요일을 가져간다 — 하루에 몰리지 않게."""
    days = plan_team_days({"큰팀": 24, "작은팀": 3}, days_per_team=2)

    assert days["큰팀"] == ["월", "화"]
    assert days["작은팀"] == ["수", "목"]


# ------------------------------------------- 담당자가 나올 수 있는 요일로 잡는가

def test_days_land_where_the_crew_can_actually_come():
    """목 · 금만 되는 팀에 월 · 화 · 수를 주지 않는다.

    인원 수만 보고 요일을 정하면, 그 팀 담당자가 한 분도 못 나오는 날이
    면접 요일이 된다. 부서 화면은 전원을 '가능하다고 하신 요일 · 시간에는
    빈 자리가 없습니다' 로 밀어 내고, 자동배치는 남는 사람에게 한 칸씩
    번갈아 얹으며, 인사팀 최종 시간표는 그 자리를 죄다 옮긴다.
    """
    days = plan_team_days({"가팀": 16}, 3, len(HOURS), {"가팀": ["목", "금"]})

    assert days["가팀"] == ["목", "금"]


def test_open_days_only_narrow_the_pool_when_seats_fit():
    """앉힐 사람이 다 들어가면 못 나오는 날을 억지로 붙이지 않는다."""
    # 8명 · 하루 8칸 → 하루면 다 앉는다. 못 나오는 날을 더 붙이면 그 하루가
    # 통째로 '인사팀 시간표에서 옮겨질 자리' 가 된다.
    assert plan_team_days({"가팀": 8}, 3, 8, {"가팀": ["금"]})["가팀"] == ["금"]


def test_days_are_filled_up_when_open_days_cannot_hold_everyone():
    """되는 요일만으로 자리가 모자라면 남은 요일로 채운다 — 빈손보다는 낫다."""
    days = plan_team_days({"가팀": 40}, 3, 8, {"가팀": ["목", "금"]})

    assert days["가팀"] == ["월", "목", "금"]      # 되는 날 먼저, 모자란 하루만 더


def test_team_with_nobody_available_falls_back_to_load():
    """아무도 못 나오는 팀은 예전처럼 한산한 요일로 — 요일이 없으면 화면이 죽는다."""
    days = plan_team_days({"가팀": 12}, 3, len(HOURS), {"가팀": []})

    assert len(days["가팀"]) == 3
    assert all(day in DAYS for day in days["가팀"])


def test_open_days_do_not_change_the_old_answer():
    """안 주거나 모두 다 되는 팀이면 예전 결과 그대로다 — 옛 호출자 보호."""
    sizes = {"가팀": 12, "나팀": 9, "다팀": 7}
    everyone = {team: list(DAYS) for team in sizes}

    assert plan_team_days(sizes) == plan_team_days(sizes, open_days=None)
    assert plan_team_days(sizes) == plan_team_days(sizes, DAYS_PER_TEAM,
                                                   len(HOURS), everyone)


# ------------------------------------------------ 정해 둔 요일을 그대로 쓰는가

def test_given_days_are_used_as_is():
    """인사가 정해 보낸 요일이 있으면 새로 뽑지 않는다."""
    given = {"가팀": ["수", "금"]}

    result = hierarchical.fixed_team_days(given, {"가팀": 10}, 3)

    assert result["가팀"] == ["수", "금"]


def test_unknown_or_repeated_days_are_cleaned():
    """이상한 요일이 섞여 오면 걸러내고, 같은 요일은 한 번만 센다."""
    given = {"가팀": ["화", "화", "토", "목"]}

    assert hierarchical.fixed_team_days(given, {"가팀": 8}, 3)["가팀"] == ["화", "목"]


def test_team_without_given_days_still_gets_some():
    """정해 둔 요일이 없는 팀만 새로 뽑는다 — 빈손으로 두지 않는다."""
    result = hierarchical.fixed_team_days({"가팀": ["수"]}, {"가팀": 5, "나팀": 5}, 2)

    assert result["가팀"] == ["수"]
    assert len(result["나팀"]) == 2


def test_no_given_days_falls_back_to_planning():
    """안 주면 예전처럼 여기서 뽑는다 — 옛 호출자 보호."""
    sizes = {"가팀": 10, "나팀": 6}

    assert hierarchical.fixed_team_days(None, sizes, 3) == plan_team_days(sizes, 3)


# ------------------------------------------------------------- 배치까지 이어지는가

def test_generated_schedule_sits_on_the_days_hr_fixed():
    """시간표가 인사가 정한 요일 위에만 앉는다."""
    people = _people("가팀", 6) + _people("나팀", 6)
    crew = _crew("가팀") + _crew("나팀")
    constraints = GenerateConstraints(days_by_team={"가팀": ["수"], "나팀": ["금"]})

    plan = algorithm_v4.run(people, crew, constraints, days_per_team=2)

    assert plan.notes["team_days"] == {"가팀": ["수"], "나팀": ["금"]}
    for assignment in plan.assignments:
        assert assignment.day == ("수" if assignment.team == "가팀" else "금")


def test_dropping_people_does_not_move_the_days():
    """부서에서 사람을 덜 골라 인원이 줄어도 요일은 그대로다."""
    crew = _crew("가팀") + _crew("나팀")
    days = {"가팀": ["화", "목"], "나팀": ["월", "수"]}
    constraints = GenerateConstraints(days_by_team=days)

    full = algorithm_v4.run(_people("가팀", 8) + _people("나팀", 8), crew, constraints)
    thin = algorithm_v4.run(_people("가팀", 2) + _people("나팀", 8), crew, constraints)

    assert full.notes["team_days"] == thin.notes["team_days"] == days


# ------------------------------------------------------ 자리를 옮길 때 가까운 칸인가

def test_moved_seat_lands_next_to_where_the_dept_put_it():
    """부서 자리를 못 지키면 가장 가까운 빈 칸으로 간다 — 아무 데나가 아니다."""
    team = "가팀"
    people = _people(team, 2)
    board_crew = _crew(team, 1)
    from app.services.board import Board

    board = Board(
        board_crew,
        pinned_by_team={team: {a.applicant_id: board_crew[0].interviewer_id
                               for a in people}},
        seats={team: {people[0].applicant_id: (1, 4),
                      people[1].applicant_id: (1, 4)}},   # 같은 칸을 둘이 잡았다
    )

    left = hierarchical.place_dept_seats(board, team, ["월"], people)

    assert left == []                                    # 둘 다 앉았다
    placed = {a.applicant_id: a.hour for a in board.assignments}
    assert placed[people[0].applicant_id] == HOURS[4]    # 먼저 온 사람은 그대로
    moved = placed[people[1].applicant_id]
    assert abs(HOURS.index(moved) - 4) == 1              # 바로 옆 칸으로 옮겼다
    tags = {a.applicant_id: a.reason_tags for a in board.assignments}
    assert "SEAT_MOVED_TAKEN" in tags[people[1].applicant_id]
