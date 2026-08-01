"""팀별 면접 요일은 인사가 명단을 보낼 때 한 번 정하고 끝이다

예전에는 시간표를 만들 때마다 요일을 다시 뽑았다. 부서에서 사람을 덜 고르면
팀 인원이 줄고, 인원이 줄면 요일 순서가 바뀌어, 같은 명단으로 두 번 만들면
서로 다른 요일의 시간표가 나왔다. 부서가 '1일차' 에 잡아 둔 자리는 그때마다
다른 요일을 가리키게 된다.

그래서 요일은 3단계(명단 보내기)에서 못 박고, 그 뒤 단계는 받아 쓰기만 한다.
"""
from __future__ import annotations

import inspect

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn
from app.infrastructure.contracts import (BAND_ALL, BAND_FRONT, DAYS, HOURS,
                                          band_hours, normalize_availability,
                                          plan_team_days)
from app.services import algorithm_v4, hierarchical

BAND = BAND_ALL


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


# ------------------------------------------------- 담당자 가능 요일은 묻지 않는가
#
# 한때는 담당자가 적어 낸 요일을 보고 팀 면접 요일을 골랐다. 그런데 그 요일은
# 어느 화면에서도 물어본 적이 없는 값이었다 — 폼은 시간 덩어리만 받았고, 요일은
# 저장 형식이 {요일: [칸]} 이라 딸려 들어간 부산물이었다. 그 부산물이 사람의
# 뜻인 양 자리를 막아 "나는 모든 시간이 된다고 했는데 왜 빈 자리가 없나" 가
# 나왔다. 지금은 가능 시간이 앞타임 · 뒤타임 · 모든타임 뿐이고, 그 덩어리는
# 어느 요일에나 똑같이 적용된다.

def test_team_days_do_not_ask_about_the_crew():
    """요일 계산에 담당자 가용성이 끼어들 자리가 아예 없다."""
    params = list(inspect.signature(plan_team_days).parameters)

    assert params == ["sizes_by_team", "days_per_team", "slots_per_day"]


def test_a_day_written_down_spreads_to_every_day():
    """'금요일에 3타임' 이라고 적어 내도 3타임은 모든 요일에 열려 있다."""
    spread = normalize_availability({"금": [HOURS[2]]})

    assert spread == {day: [HOURS[2]] for day in DAYS}


def test_which_day_it_was_written_on_changes_nothing():
    """어느 요일 칸에 적혔는지는 결과를 바꾸지 않는다 — 칸만 남는다."""
    front = list(band_hours(BAND_FRONT))

    assert (normalize_availability({"월": front})
            == normalize_availability({"목": front})
            == {day: front for day in DAYS})


def test_a_crew_that_only_wrote_one_day_still_fills_another():
    """'금' 에만 적어 낸 팀도 인사가 정한 요일(수)에 그대로 앉는다.

    예전 같으면 이 담당자는 수요일에 못 나오는 사람이라 자리가 통째로 비었다.
    """
    people = _people("가팀", 4)
    crew = [InterviewerIn(interviewer_id="가팀-IV0", name="가팀-IV0", team="가팀",
                          max_daily=len(HOURS), priority=2,
                          availability=normalize_availability({"금": list(HOURS)}))]
    constraints = GenerateConstraints(days_by_team={"가팀": ["수"]})

    plan = algorithm_v4.run(people, crew, constraints, days_per_team=1)

    assert len(plan.assignments) == len(people)
    assert {a.day for a in plan.assignments} == {"수"}


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
