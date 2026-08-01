"""규칙1의 목표 비율 — 재는 것은 "요일 분산" 이지 3할이 아니다.

원래 목적은 "학사 / 석·박사 간 실력 편중이 발생하지 않도록 요일을 분산" 하는
것이다. 3할이라는 숫자는 그 목적을 재기 위해 골랐던 잣대일 뿐이라, 명단이
3할과 다르면 잣대 쪽이 틀린 것이다. 그래서 목표를 안 주면 명단의 실제
비율로 잰다.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn, InterviewerIn
from app.infrastructure.contracts import HOURS
from app.services.board import Board
from app.services.hierarchical import fallback_place, split_team_by_day
from app.services.rule_evaluator import rule1_grad_balance

from tests.test_rule_evaluator import A


def _day(day: str, total: int, grads: int) -> list[dict]:
    return [
        A(degree="대학원" if i < grads else "학사", day=day,
          applicant_id=f"{day}-{i}")
        for i in range(total)
    ]


def test_evenly_split_low_grad_round_is_not_blamed():
    """대학원생이 반 할뿐인 회차를 고르게 나누면 만점이다.

    3할로 못 박으면 어느 날도 허용 범위(1~5할)에 못 들어 온 날이 위반 —
    0점이 나온다. 고르게 나눈 시간표를 0점으로 두면 점수가 편중을 재는 것을
    그만둔 것이다.
    """
    rows = _day("1일차", 20, 1) + _day("2일차", 20, 1) + _day("3일차", 20, 1)

    score, detail = rule1_grad_balance(rows, target=None, tolerance=0.20)
    assert score == 100.0
    assert detail["outside"] == []
    assert detail["target"] == 0.05
    assert detail["target_source"] == "명단"

    # 3할로 못 박던 옛 잣대는 같은 시간표를 전부 위반으로 잡았다
    old, old_detail = rule1_grad_balance(rows, target=0.30, tolerance=0.20)
    assert old == 0.0
    assert old_detail["outside"] == ["1일차", "2일차", "3일차"]
    assert old_detail["target_source"] == "지정"


def test_skewed_round_still_fails_without_a_fixed_target():
    """명단 비율을 목표로 삼아도 한쪽으로 몰린 시간표는 잡아야 한다.

    목표를 명단에서 가져오는 것이 '늘 만점' 을 뜻하면 규칙1이 없는 것과 같다.
    """
    rows = _day("1일차", 20, 0) + _day("2일차", 20, 6) + _day("3일차", 20, 12)

    score, detail = rule1_grad_balance(rows, target=None, tolerance=0.20)
    assert detail["target"] == 0.3               # 18/60 — 명단이 정한 목표
    assert detail["outside"] == ["1일차", "3일차"]  # 0할 · 6할이 허용 밖
    assert score == round(100 * 1 / 3, 1)


def test_a_pinned_target_is_still_obeyed():
    """인사가 숫자를 못 박으면 명단이 어떻든 그 숫자로 잰다."""
    rows = _day("1일차", 10, 5) + _day("2일차", 10, 5)

    score, detail = rule1_grad_balance(rows, target=0.30, tolerance=0.10)
    assert detail["target"] == 0.3
    assert detail["target_source"] == "지정"
    assert detail["outside"] == ["1일차", "2일차"]
    assert score == 0.0


def _members(team: str, grads: int, bachelors: int) -> list[ApplicantIn]:
    rows = [ApplicantIn(applicant_id=f"{team}-G{i}", name=f"석박{i}", team=team,
                        degree="대학원") for i in range(grads)]
    rows += [ApplicantIn(applicant_id=f"{team}-B{i}", name=f"학사{i}", team=team,
                         degree="학사") for i in range(bachelors)]
    return rows


def test_a_team_splits_by_its_own_ratio():
    """팀을 날로 나눌 때도 그 팀이 가진 비율대로 나눈다.

    대학원생 8명 · 학사 2명인 팀의 두 날은 8할씩이어야 서로 닮는다. 한 날을
    3할로 맞추려 들면 나머지 대학원생이 다른 날로 몰려 되레 편중이 커진다.
    """
    team = "AI솔루션팀"
    days = ["1일차", "2일차"]
    groups, leftover = split_team_by_day(_members(team, 8, 2), days)

    assert leftover == []
    ratios = [sum(1 for a in groups[d] if a.is_grad) / len(groups[d]) for d in days]
    assert ratios == [0.8, 0.8]


def test_a_team_with_only_grads_is_split_evenly():
    """대학원생만 있는 팀은 어떤 고정값으로도 목표를 맞출 수 없다 — 그냥 반씩."""
    team = "미래혁신팀"
    days = ["1일차", "2일차"]
    groups, leftover = split_team_by_day(_members(team, 6, 0), days)

    assert leftover == []
    assert [len(groups[d]) for d in days] == [3, 3]
    assert all(a.is_grad for d in days for a in groups[d])


def test_a_pinned_target_still_drives_the_split():
    """숫자를 주면 팀 비율 대신 그 숫자로 나눈다 — 인사가 못 박은 값이다."""
    days = ["1일차", "2일차"]
    # 팀 비율은 반반. 이틀에 16석뿐이라 8명은 남는다 — 누구를 앉힐지가 갈린다.
    members = _members("전극기술팀", 12, 12)

    pinned, _ = split_team_by_day(members, days, 0.25)
    assert [sum(1 for a in pinned[d] if a.is_grad) for d in days] == [2, 2]

    # 안 주면 팀 비율(반반)대로 — 같은 명단인데 나뉘는 수가 다르다
    own, _ = split_team_by_day(members, days)
    assert [sum(1 for a in own[d] if a.is_grad) for d in days] == [4, 4]


def _board_with(team: str, seated: dict[str, list[str]]) -> Board:
    """{면접일: [학위]} 대로 앞칸부터 채워 둔 판을 만든다."""
    board = Board(
        [InterviewerIn(interviewer_id="IV001", name="담당", team=team, max_daily=len(HOURS))],
        ignore_availability=True,
    )
    for day, degrees in seated.items():
        for i, degree in enumerate(degrees):
            board.place(
                ApplicantIn(applicant_id=f"{day}-{i}", name=f"{day}-{i}",
                            team=team, degree=degree),
                day, HOURS[i], ["PRIMARY_JOB"],
            )
    return board


def test_leftovers_land_on_the_day_that_evens_the_round_out():
    """마지막에 남은 사람도 회차 비율을 고르게 만드는 날로 간다.

    1일차에만 대학원생이 있고 2일차는 학사뿐인 판이다. 회차 비율(2/11)을
    목표로 보면 남은 대학원생은 2일차로 가야 두 날이 닮는다. 3할로 못 박으면
    두 날 다 3할에 못 미치므로 '이미 대학원생이 있는 1일차' 쪽이 목표에 가까워
    보여, 편중을 더 키우는 쪽으로 앉힌다.
    """
    team = "AI솔루션팀"
    board = _board_with(team, {
        "1일차": ["대학원", "학사", "학사", "학사", "학사"],
        "2일차": ["학사", "학사", "학사", "학사", "학사"],
    })
    leftover = ApplicantIn(applicant_id="L1", name="남은사람", team=team, degree="대학원")

    placed, still = fallback_place(board, [leftover])
    assert still == []
    assert [(a.day, a.hour) for a in placed] == [("2일차", HOURS[5])]

    # 3할을 못 박으면 반대쪽 — 대학원생이 이미 있는 날에 더 얹는다
    board2 = _board_with(team, {
        "1일차": ["대학원", "학사", "학사", "학사", "학사"],
        "2일차": ["학사", "학사", "학사", "학사", "학사"],
    })
    pinned, _ = fallback_place(board2, [leftover], 0.30)
    assert [a.day for a in pinned] == ["1일차"]
