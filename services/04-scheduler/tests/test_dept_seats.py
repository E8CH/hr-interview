"""부서가 잡아 준 자리를 최종 시간표가 물려받는다

부서 화면(② 매칭 · ③ 우리 팀 시간표)에서 확인하고 보낸 자리가 최종 시간표의
출발점이 되어야 한다. 예전에는 짝만 넘어가고 요일 · 시각은 여기서 처음부터
새로 짜서, 부서가 보고 보낸 시간표와 최종본이 서로 다른 물건이었다.

원칙은 둘이다.
  - 지킬 수 있으면 그대로 지킨다.
  - 못 지키면 억지로 밀어 넣지 않고 옮기되, 왜 옮겼는지를 배정 사유에 남긴다.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn, GenerateConstraints, InterviewerIn
from app.infrastructure.contracts import BAND_ALL, BAND_BACK, DAYS, HOURS, band_hours
from app.services import algorithm_v5, hierarchical
from app.services.board import Board
from app.services.constraint_checker import check_hard_constraints

TEAM = "배터리기술팀"


def _interviewer(interviewer_id: str, band: str = BAND_ALL) -> InterviewerIn:
    """가능 시간을 못 박은 담당자 — 모의 명단은 사람마다 덩어리가 달라 흔들린다."""
    return InterviewerIn(
        interviewer_id=interviewer_id, name=interviewer_id, team=TEAM,
        max_daily=len(HOURS), priority=2,
        availability={day: list(band_hours(band)) for day in DAYS},
    )


def _people(count: int) -> list[ApplicantIn]:
    return [
        ApplicantIn(applicant_id=f"S{i:02d}", name=f"자리{i:02d}", team=TEAM, degree="학사")
        for i in range(1, count + 1)
    ]


def _pairs(people, interviewer_id: str) -> dict[str, dict[str, str]]:
    return {TEAM: {a.applicant_id: interviewer_id for a in people}}


# ------------------------------------------------------------ 그대로 지키는가

def test_dept_seat_is_kept_as_sent():
    """부서가 5번째 칸이라고 보냈으면 최종 시간표도 5번째 칸이다."""
    people = _people(3)
    board = Board([_interviewer("IV_ALL")], pinned_by_team=_pairs(people, "IV_ALL"),
                  seats={TEAM: {"S01": (1, 4), "S02": (1, 2), "S03": (1, 0)}})

    left = hierarchical.place_dept_seats(board, TEAM, ["수"], people)

    assert left == []
    placed = {a.applicant_id: (a.day, a.hour) for a in board.assignments}
    assert placed["S01"] == ("수", HOURS[4])
    assert placed["S02"] == ("수", HOURS[2])
    assert placed["S03"] == ("수", HOURS[0])
    for assignment in board.assignments:
        assert "DEPT_SEAT" in assignment.reason_tags


def test_dept_day_number_follows_the_days_hr_picked():
    """부서는 '몇 일차' 로만 세고, 어느 요일인지는 인사팀이 정한다."""
    people = _people(2)
    board = Board([_interviewer("IV_ALL")], pinned_by_team=_pairs(people, "IV_ALL"),
                  seats={TEAM: {"S01": (1, 0), "S02": (2, 0)}})

    assert hierarchical.place_dept_seats(board, TEAM, ["화", "목"], people) == []

    placed = {a.applicant_id: a.day for a in board.assignments}
    assert placed["S01"] == "화"      # 1일차 → 그 팀의 첫 면접 요일
    assert placed["S02"] == "목"      # 2일차 → 두 번째 면접 요일


# ------------------------------------------------------- 못 지킬 때 까닭을 남기는가

def test_seat_outside_interviewer_hours_moves_and_says_why():
    """뒤타임만 되는 담당자에게 첫 칸을 보내면 그분 시간 안으로 당겨 앉힌다."""
    people = _people(1)
    board = Board([_interviewer("IV_BACK", BAND_BACK)],
                  pinned_by_team=_pairs(people, "IV_BACK"),
                  seats={TEAM: {"S01": (1, 0)}})   # 첫 칸 = 뒤타임 밖

    left = hierarchical.place_dept_seats(board, TEAM, ["월"], people)

    assert left == []                                    # 여기서 앉혔다
    assert board.seat_miss["S01"] == "SEAT_MOVED_BAND"
    moved = board.assignments[0]
    # 부서가 말한 칸에서 가장 가까운, 담당자가 되는 칸이다
    assert moved.hour == band_hours(BAND_BACK)[0]
    # 옮긴 까닭이 배정 사유로 따라가고, '부서가 정한 자리' 는 떼어 낸다
    assert "SEAT_MOVED_BAND" in moved.reason_tags
    assert "DEPT_SEAT" not in moved.reason_tags


def test_seat_already_taken_says_so():
    """두 사람을 같은 칸에 보내면 뒷사람만 옆 칸으로 밀고 '이미 찼다' 고 적는다."""
    people = _people(2)
    board = Board([_interviewer("IV_ALL")], pinned_by_team=_pairs(people, "IV_ALL"),
                  seats={TEAM: {"S01": (1, 3), "S02": (1, 3)}})

    left = hierarchical.place_dept_seats(board, TEAM, ["월"], people)

    assert left == []
    assert board.seat_miss["S02"] == "SEAT_MOVED_TAKEN"
    placed = {a.applicant_id: a.hour for a in board.assignments}
    assert placed["S01"] == HOURS[3]                     # 먼저 온 사람은 그대로
    assert abs(HOURS.index(placed["S02"]) - 3) == 1      # 바로 옆 칸


def test_seat_over_the_daily_cap_says_so():
    """하루 두 명까지인 담당자에게 세 자리를 보내면 세 번째는 한도 때문에 옮긴다."""
    tight = InterviewerIn(
        interviewer_id="IV_CAP", name="IV_CAP", team=TEAM, max_daily=2, priority=2,
        availability={day: list(band_hours(BAND_ALL)) for day in DAYS},
    )
    people = _people(3)
    board = Board([tight], pinned_by_team=_pairs(people, "IV_CAP"),
                  seats={TEAM: {"S01": (1, 0), "S02": (1, 2), "S03": (1, 4)}})

    left = hierarchical.place_dept_seats(board, TEAM, ["월"], people)

    assert [a.applicant_id for a in left] == ["S03"]
    assert board.seat_miss["S03"] == "SEAT_MOVED_CAP"


def test_day_beyond_the_teams_interview_days_moves():
    """부서가 4일차라고 보냈는데 그 팀 면접이 이틀뿐이면 마지막 날로 당긴다."""
    people = _people(1)
    board = Board([_interviewer("IV_ALL")], pinned_by_team=_pairs(people, "IV_ALL"),
                  seats={TEAM: {"S01": (4, 0)}})

    left = hierarchical.place_dept_seats(board, TEAM, ["월", "화"], people)

    assert left == []
    assert board.seat_miss["S01"] == "SEAT_MOVED_DAY"
    moved = board.assignments[0]
    assert (moved.day, moved.hour) == ("화", HOURS[0])   # 4일차에 가장 가까운 날
    assert "SEAT_MOVED_DAY" in moved.reason_tags


# ------------------------------------------------------------------ 전체 배치

def test_v5_honours_dept_seats_without_breaking_hard_rules():
    """자리를 물려받아도 하드 제약은 그대로 0이어야 한다."""
    people = _people(4)
    crew = [_interviewer("IV_ALL"), _interviewer("IV_ALL2")]
    constraints = GenerateConstraints(
        pairs_by_team=_pairs(people, "IV_ALL"),
        seats_by_team={
            TEAM: {a.applicant_id: {"day": 1, "slot": index}
                   for index, a in enumerate(people)}
        },
    )
    assert constraints.seat_map() == {
        TEAM: {a.applicant_id: (1, index) for index, a in enumerate(people)}
    }

    plan = algorithm_v5.run(list(people), crew, constraints)

    assert check_hard_constraints(plan.assignments, crew) == []
    assert plan.notes["dept_seats"] == len(people)
    assert plan.notes["dept_seats_kept"] == len(people)
    assert plan.notes["dept_seats_moved"] == {}
    # 지킨 자리는 부서가 말한 칸 그대로다
    wanted = {a.applicant_id: HOURS[index] for index, a in enumerate(people)}
    for assignment in plan.assignments:
        assert "DEPT_SEAT" in assignment.reason_tags
        assert assignment.hour == wanted[assignment.applicant_id]
    assert len({a.day for a in plan.assignments}) == 1


def test_no_seats_means_the_old_behaviour(applicants, interviewers):
    """자리를 안 보내면 예전처럼 시간표가 알아서 짠다 — 옛 호출자 보호."""
    plan = algorithm_v5.run(applicants, interviewers, GenerateConstraints())

    assert plan.notes["dept_seats"] == 0
    assert plan.notes["dept_seats_moved"] == {}
    assert plan.assignments
    assert all("DEPT_SEAT" not in a.reason_tags for a in plan.assignments)
