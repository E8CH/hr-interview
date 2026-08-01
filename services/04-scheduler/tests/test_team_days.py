"""팀별 면접일은 인사가 명단을 보낼 때 한 번 정하고 끝이다

날 이름은 요일이 아니라 '1일차 … 5일차' 다. 무슨 요일에 보는지는 우리가 셈에
넣는 값이 아니다.

두 가지를 여기서 지킨다.

① **어느 팀이나 1일차부터 본다.** 예전에는 팀마다 '한산한 날' 을 나눠 줘서 큰
   팀이 앞날을 먼저 가져가고 남는 팀은 3일차에야 첫 면접을 봤다 — "우리 팀은 왜
   첫날 면접이 없나" 가 거기서 나왔다. 날을 비켜 준다고 얻는 것이 없다: 담당자는
   팀마다 따로고, 겹침도 (팀, 날, 칸) 안에서만 따진다.

② **한 번 정한 날은 안 움직인다.** 예전에는 시간표를 만들 때마다 날을 다시
   뽑았다. 부서에서 사람을 덜 고르면 팀 인원이 줄고, 인원이 줄면 날 순서가 바뀌어
   같은 명단으로 두 번 만들면 서로 다른 시간표가 나왔다. 그래서 날은 3단계(명단
   보내기)에서 못 박고, 그 뒤 단계는 받아 쓰기만 한다.
"""
from __future__ import annotations

import inspect

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn
from app.infrastructure.contracts import (BAND_ALL, BAND_FRONT, DAYS, HOURS,
                                          band_hours, day_name,
                                          normalize_availability,
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


# ------------------------------------------------------- 어느 팀이나 1일차부터인가

def test_every_team_starts_on_day_one():
    """어떤 팀도 첫날을 건너뛰지 않는다.

    사건: 인사 화면에서 미래혁신팀 · AI솔루션팀만 1일차에 면접이 하나도 없었다.
    까닭은 '한산한 날' 을 큰 팀부터 나눠 준 계산이었다 — 로봇응용기술팀(24명)이
    1~3일차를 가져가자 뒤에 남은 팀은 3일차 이후만 받았다. 팀끼리 날을 비켜 줘도
    자리가 늘지 않으므로 지금은 다 같이 1일차에 시작한다.
    """
    days = plan_team_days({"로봇응용기술팀": 24, "배터리기술팀": 16, "전극기술팀": 16,
                           "AI솔루션팀": 8, "미래혁신팀": 8})

    assert all(team_days[0] == DAYS[0] for team_days in days.values())
    assert all(team_days == DAYS[:3] for team_days in days.values())


def test_a_small_team_gets_as_many_days_as_a_big_one():
    """인원이 적다고 날을 덜 받지 않는다 — 날 수는 팀 크기와 무관하다."""
    days = plan_team_days({"큰팀": 24, "작은팀": 3}, days_per_team=2)

    assert days["큰팀"] == days["작은팀"] == DAYS[:2]


def test_days_never_run_past_the_calendar():
    """달력에 있는 날보다 길게 잡지 않는다."""
    days = plan_team_days({"가팀": 40}, days_per_team=99)

    assert days["가팀"] == list(DAYS)


def test_legacy_weekday_data_is_read_as_a_day_number():
    """요일로 저장해 둔 옛 회차 자료도 지금 이름으로 읽힌다.

    옛 회차의 시간표 · 인계 파일에는 날이 '월 · 화' 로 적혀 있다. 그대로 읽으면
    달력에 없는 날이 되어 그 회차가 통째로 안 열린다.
    """
    assert [day_name(d) for d in ["월", "화", "수", "목", "금"]] == list(DAYS)
    assert day_name("3일차") == "3일차"        # 지금 이름은 그대로
    assert day_name("토") == "토"              # 모르는 값은 건드리지 않는다


# ------------------------------------------------- 담당자 가능 요일은 묻지 않는가
#
# 한때는 담당자가 적어 낸 요일을 보고 팀 면접일을 골랐다. 그런데 그 요일은 어느
# 화면에서도 물어본 적이 없는 값이었다 — 폼은 시간 덩어리만 받았고, 요일은 저장
# 형식이 {날: [칸]} 이라 딸려 들어간 부산물이었다. 그 부산물이 사람의 뜻인 양
# 자리를 막아 "나는 모든 시간이 된다고 했는데 왜 빈 자리가 없나" 가 나왔다.
# 지금은 가능 시간이 앞타임 · 뒤타임 · 모든타임 뿐이고, 그 덩어리는 어느 날에나
# 똑같이 적용된다.

def test_team_days_do_not_ask_about_the_crew():
    """날 계산에 담당자 가용성이 끼어들 자리가 아예 없다."""
    params = list(inspect.signature(plan_team_days).parameters)

    assert params == ["sizes_by_team", "days_per_team"]


def test_a_day_written_down_spreads_to_every_day():
    """'5일차에 3타임' 이라고 적어 내도 3타임은 모든 날에 열려 있다."""
    spread = normalize_availability({"5일차": [HOURS[2]]})

    assert spread == {day: [HOURS[2]] for day in DAYS}


def test_which_day_it_was_written_on_changes_nothing():
    """어느 날 칸에 적혔는지는 결과를 바꾸지 않는다 — 칸만 남는다."""
    front = list(band_hours(BAND_FRONT))

    assert (normalize_availability({"1일차": front})
            == normalize_availability({"4일차": front})
            == {day: front for day in DAYS})


def test_a_crew_that_only_wrote_one_day_still_fills_another():
    """'5일차' 에만 적어 낸 팀도 인사가 정한 날(3일차)에 그대로 앉는다.

    예전 같으면 이 담당자는 3일차에 못 나오는 사람이라 자리가 통째로 비었다.
    """
    people = _people("가팀", 4)
    crew = [InterviewerIn(interviewer_id="가팀-IV0", name="가팀-IV0", team="가팀",
                          max_daily=len(HOURS), priority=2,
                          availability=normalize_availability({"5일차": list(HOURS)}))]
    constraints = GenerateConstraints(days_by_team={"가팀": ["3일차"]})

    plan = algorithm_v4.run(people, crew, constraints, days_per_team=1)

    assert len(plan.assignments) == len(people)
    assert {a.day for a in plan.assignments} == {"3일차"}


# -------------------------------------------------- 정해 둔 날을 그대로 쓰는가

def test_given_days_are_used_as_is():
    """인사가 정해 보낸 날이 있으면 새로 뽑지 않는다."""
    given = {"가팀": ["3일차", "5일차"]}

    result = hierarchical.fixed_team_days(given, {"가팀": 10}, 3)

    assert result["가팀"] == ["3일차", "5일차"]


def test_unknown_or_repeated_days_are_cleaned():
    """이상한 날이 섞여 오면 걸러내고, 같은 날은 한 번만 센다."""
    given = {"가팀": ["2일차", "2일차", "8일차", "4일차"]}

    assert hierarchical.fixed_team_days(given, {"가팀": 8}, 3)["가팀"] == ["2일차", "4일차"]


def test_team_without_given_days_still_gets_some():
    """정해 둔 날이 없는 팀만 새로 뽑는다 — 빈손으로 두지 않는다."""
    result = hierarchical.fixed_team_days({"가팀": ["3일차"]}, {"가팀": 5, "나팀": 5}, 2)

    assert result["가팀"] == ["3일차"]
    assert len(result["나팀"]) == 2


def test_no_given_days_falls_back_to_planning():
    """안 주면 예전처럼 여기서 뽑는다 — 옛 호출자 보호."""
    sizes = {"가팀": 10, "나팀": 6}

    assert hierarchical.fixed_team_days(None, sizes, 3) == plan_team_days(sizes, 3)


# ------------------------------------------------------------- 배치까지 이어지는가

def test_generated_schedule_sits_on_the_days_hr_fixed():
    """시간표가 인사가 정한 날 위에만 앉는다."""
    people = _people("가팀", 6) + _people("나팀", 6)
    crew = _crew("가팀") + _crew("나팀")
    constraints = GenerateConstraints(days_by_team={"가팀": ["3일차"], "나팀": ["5일차"]})

    plan = algorithm_v4.run(people, crew, constraints, days_per_team=2)

    assert plan.notes["team_days"] == {"가팀": ["3일차"], "나팀": ["5일차"]}
    for assignment in plan.assignments:
        assert assignment.day == ("3일차" if assignment.team == "가팀" else "5일차")


def test_dropping_people_does_not_move_the_days():
    """부서에서 사람을 덜 골라 인원이 줄어도 날은 그대로다."""
    crew = _crew("가팀") + _crew("나팀")
    days = {"가팀": ["2일차", "4일차"], "나팀": ["1일차", "3일차"]}
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

    left = hierarchical.place_dept_seats(board, team, ["1일차"], people)

    assert left == []                                    # 둘 다 앉았다
    placed = {a.applicant_id: a.hour for a in board.assignments}
    assert placed[people[0].applicant_id] == HOURS[4]    # 먼저 온 사람은 그대로
    moved = placed[people[1].applicant_id]
    assert abs(HOURS.index(moved) - 4) == 1              # 바로 옆 칸으로 옮겼다
    tags = {a.applicant_id: a.reason_tags for a in board.assignments}
    assert "SEAT_MOVED_TAKEN" in tags[people[1].applicant_id]
