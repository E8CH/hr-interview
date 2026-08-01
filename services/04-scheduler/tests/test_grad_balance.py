"""규칙1 — 재는 것은 "요일 분산" 이지 3할이 아니고, 편중은 팀 안에서 생긴다.

원래 목적은 "학사 / 석·박사 간 실력 편중이 발생하지 않도록 요일을 분산" 하는
것이다. 3할이라는 숫자는 그 목적을 재기 위해 골랐던 잣대일 뿐이라, 명단이
3할과 다르면 잣대 쪽이 틀린 것이다. 그래서 목표를 안 주면 명단의 실제
비율로 잰다.

무엇을 한 칸으로 보느냐도 같은 물음이다. 사람을 견주는 일은 팀 안에서 일어나므로
(팀, 면접일)로 잰다 — 회차 전체의 날별 비율은 팀마다 면접일이 다르면 튄다.
"""
from __future__ import annotations

from app.domain.schemas import ApplicantIn, InterviewerIn
from app.infrastructure.contracts import HOURS
from app.services.board import Board
from app.services.hierarchical import fallback_place, split_team_by_day
from app.services.rule_evaluator import rule1_grad_balance

from tests.test_rule_evaluator import A


def _day(day: str, total: int, grads: int, team: str = "AI솔루션팀") -> list[dict]:
    return [
        A(team=team, degree="대학원" if i < grads else "학사", day=day,
          applicant_id=f"{team}-{day}-{i}")
        for i in range(total)
    ]


def _days_out(detail) -> list[str]:
    return [row["day"] for row in detail["outside"]]


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
    assert detail["teams"]["AI솔루션팀"]["target"] == 0.05
    assert detail["target_source"] == "명단"

    # 3할로 못 박던 옛 잣대는 같은 시간표를 전부 위반으로 잡았다
    old, old_detail = rule1_grad_balance(rows, target=0.30, tolerance=0.20)
    assert old == 0.0
    assert _days_out(old_detail) == ["1일차", "2일차", "3일차"]
    assert old_detail["target_source"] == "지정"


def test_skewed_round_still_fails_without_a_fixed_target():
    """명단 비율을 목표로 삼아도 한쪽으로 몰린 시간표는 잡아야 한다.

    목표를 명단에서 가져오는 것이 '늘 만점' 을 뜻하면 규칙1이 없는 것과 같다.
    """
    rows = _day("1일차", 20, 0) + _day("2일차", 20, 6) + _day("3일차", 20, 12)

    score, detail = rule1_grad_balance(rows, target=None, tolerance=0.20)
    assert detail["teams"]["AI솔루션팀"]["target"] == 0.3   # 18/60 — 명단이 정한 목표
    assert _days_out(detail) == ["1일차", "3일차"]           # 0할 · 6할이 허용 밖
    assert score == round(100 * 1 / 3, 1)


def test_a_pinned_target_is_still_obeyed():
    """인사가 숫자를 못 박으면 명단이 어떻든 그 숫자로 잰다."""
    rows = _day("1일차", 10, 5) + _day("2일차", 10, 5)

    score, detail = rule1_grad_balance(rows, target=0.30, tolerance=0.10)
    assert detail["target"] == 0.3
    assert detail["target_source"] == "지정"
    assert _days_out(detail) == ["1일차", "2일차"]
    assert score == 0.0


def test_teams_even_on_their_own_days_are_not_blamed_for_a_lopsided_day():
    """팀마다 자기 날들에 고르면, 회차 전체의 날별 비율이 튀어도 만점이다.

    실제로 났던 일이다. 로봇팀이 1~3일차, 배터리팀이 1~2일차를 쓰면 3일차에는
    로봇팀의 넘친 인원만 앉는다. 그 날 비율은 혼자 튀지만 **어느 팀도 자기
    사람을 한 날로 몰지 않았다.** 인사가 자리를 어떻게 옮겨도 못 고치는 값을
    벌하면 점수를 곧 안 믿게 된다.
    """
    rows = (
        _day("1일차", 8, 3, "로봇응용기술팀")
        + _day("2일차", 8, 3, "로봇응용기술팀")
        + _day("3일차", 8, 2, "로봇응용기술팀")   # 넘친 인원만 앉는 날
        + _day("1일차", 8, 8, "배터리팀")
        + _day("2일차", 8, 8, "배터리팀")
    )

    score, detail = rule1_grad_balance(rows, target=None, tolerance=0.20)
    assert score == 100.0
    assert detail["outside"] == []
    # 3일차는 회차 전체로 보면 혼자 25% 다 — 채점하지 않고 참고로만 적는다
    assert detail["day_ratios"]["3일차"] == 0.25
    assert detail["day_ratios"]["1일차"] == round(11 / 16, 4)

    # 회차 전체의 날별 비율로 재던 옛 잣대는 이 시간표를 깎았다
    old_days = [d for d, r in detail["day_ratios"].items()
                if abs(r - detail["round_ratio"]) > 0.20 + 1e-9]
    assert old_days == ["3일차"]


def test_a_team_piling_its_grads_on_one_day_is_caught():
    """회차 전체로는 고른데 한 팀이 몰린 경우 — 이것이 진짜 편중이다.

    날마다 대학원 5할이라 회차 전체로는 흠이 없다. 그러나 A팀은 1일차에
    대학원생만, 2일차에 학사만 앉혔다. 그 날 면접은 저희끼리만 견주는 자리가
    된다. 날별로만 재던 옛 잣대는 이것을 **한 건도 못 잡았다.**
    """
    rows = (
        _day("1일차", 8, 8, "A팀") + _day("2일차", 8, 0, "A팀")
        + _day("1일차", 8, 0, "B팀") + _day("2일차", 8, 8, "B팀")
    )

    score, detail = rule1_grad_balance(rows, target=None, tolerance=0.20)
    assert score == 0.0
    assert [(row["team"], row["day"]) for row in detail["outside"]] == [
        ("A팀", "1일차"), ("A팀", "2일차"), ("B팀", "1일차"), ("B팀", "2일차"),
    ]
    # 날별로만 보면 두 날 다 정확히 5할 — 옛 잣대는 만점을 줬다
    assert detail["day_ratios"] == {"1일차": 0.5, "2일차": 0.5}


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


def _board_of_teams(seated: dict[tuple[str, str], list[str]]) -> Board:
    """{(팀, 면접일): [학위]} 대로 앞칸부터 채워 둔 판 — 팀이 여럿이다."""
    teams = sorted({team for team, _day in seated})
    board = Board(
        [InterviewerIn(interviewer_id=f"IV{i:03d}", name=f"담당{i}", team=team,
                       max_daily=len(HOURS))
         for i, team in enumerate(teams)],
        ignore_availability=True,
    )
    for (team, day), degrees in seated.items():
        for i, degree in enumerate(degrees):
            board.place(
                ApplicantIn(applicant_id=f"{team}-{day}-{i}", name=f"{team}{i}",
                            team=team, degree=degree),
                day, HOURS[i], ["PRIMARY_JOB"],
            )
    return board


def test_a_leftover_follows_its_own_team_not_the_round():
    """남은 사람은 **자기 팀** 을 고르게 만드는 날로 간다.

    두 날 모두 회차 전체로는 정확히 5할이라 회차 비율로는 어느 날이나 같아
    보인다(앞 날이 뽑힌다). 그러나 A팀은 1일차에 대학원생만 앉혀 둔 상태다 —
    거기에 대학원생을 더 얹으면 그 팀의 편중이 커진다. 규칙1을 (팀, 날)로
    재므로 여기서도 팀을 봐야 이 단계가 점수를 스스로 깎지 않는다.
    """
    board = _board_of_teams({
        ("A팀", "1일차"): ["대학원"] * 4,
        ("A팀", "2일차"): ["학사"] * 4,
        ("B팀", "1일차"): ["학사"] * 4,
        ("B팀", "2일차"): ["대학원"] * 4,
    })
    leftover = ApplicantIn(applicant_id="L1", name="남은사람", team="A팀", degree="대학원")

    placed, still = fallback_place(board, [leftover])
    assert still == []
    assert [a.day for a in placed] == ["2일차"]
