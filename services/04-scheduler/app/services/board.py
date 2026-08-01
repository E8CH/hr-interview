"""배치 보드 — 하드 제약을 구조적으로 위반할 수 없게 만드는 공용 엔진

모든 알고리즘(v1/v4/v5)은 Board.place()만 통해 배치한다.
place()는 아래를 모두 만족할 때만 성공하므로 하드 위반이 0으로 유지된다.
  1) 규칙2(HARD): 같은 팀 동시간 중복 금지 → (team, day, hour) 유일
  2) 면접관 시간 충돌 금지 → (interviewer, day, hour) 유일
  3) 일일 타임 한도 초과 금지 → min(max_daily, MAX_SLOTS_PER_DAY)
  4) 면접관 가용성 밖 배정 금지
  5) 지원자 시간 충돌 금지 → (applicant, day, hour) 유일

5)는 두 팀이 같이 보는 사람 때문에 생겼다. 그런 사람은 02에서 자리가 둘로 오므로
같은 사람이 같은 시각에 두 팀 면접장에 앉는 시간표가 나올 수 있었다 — 팀도 면접관도
서로 다르니 1)·2)로는 걸리지 않는다.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from app.domain.schemas import ApplicantIn, InterviewerIn, PlannedAssignment
from app.infrastructure.contracts import DAYS, HOURS

#: 한 담당자가 하루에 볼 수 있는 최대 면접 수 — 하루 칸 수와 같다
MAX_SLOTS_PER_DAY = len(HOURS)


class Board:
    def __init__(
        self,
        interviewers: list[InterviewerIn],
        max_daily_default: int = MAX_SLOTS_PER_DAY,
        pinned: dict[str, str] | None = None,
        pinned_by_team: dict[str, dict[str, str]] | None = None,
        ignore_availability: bool = False,
        seats: dict[str, dict[str, tuple[int, int]]] | None = None,
    ) -> None:
        self.max_daily_default = max_daily_default
        # 담당자가 적어 낸 가능 시간을 무시하고 자리부터 채운다. 앞타임만 되는
        # 사람뿐이라 늦은 자리가 빌 때, 인사가 "일단 채우고 나중에 조율" 을
        # 고르면 켜진다 — 자리는 다 차지만 담당자 사정과는 어긋날 수 있다.
        self.ignore_availability = bool(ignore_availability)
        # 부서가 확정해 보낸 짝 (지원자 → 면접 담당자). 이 사람들은 담당자를
        # 고르지 않고 정해진 사람에게만 붙인다 — 시간(면접일·시각)만 찾는다.
        self.pinned: dict[str, str] = dict(pinned or {})
        # 팀별 짝. 두 팀이 같이 보는 사람은 팀마다 담당자가 다르므로 지원자
        # 번호만으로는 한쪽을 덮어써 버린다 — 팀 자리를 먼저 보고, 그 팀 것이
        # 없을 때만 위 pinned 로 되돌아간다.
        self.pinned_by_team: dict[str, dict[str, str]] = {
            team: dict(pairs) for team, pairs in (pinned_by_team or {}).items()
        }
        # 부서가 자기 시간표에서 잡아 둔 자리 {팀: {지원자: (몇 일차, 몇 번째 칸)}}.
        # 부서 화면은 '1일차 · 2일차' 로만 세므로 일차 번호로 온다 — 그 팀에
        # 잡힌 날 중 몇째인지는 팀별 면접일을 정한 뒤에 옮겨 읽는다.
        self.seats: dict[str, dict[str, tuple[int, int]]] = {
            team: {a: (int(d), int(s)) for a, (d, s) in (wish or {}).items()}
            for team, wish in (seats or {}).items()
        }
        # 그 자리에 못 앉힌 사람과 그 까닭 — 다른 자리에 앉을 때 사유로 따라간다
        self.seat_miss: dict[str, str] = {}
        self.by_team: dict[str, list[InterviewerIn]] = defaultdict(list)
        self.by_id: dict[str, InterviewerIn] = {}
        for iv in interviewers:
            self.by_team[iv.team].append(iv)
            self.by_id[iv.interviewer_id] = iv

        self.team_slot: dict[tuple[str, str, str], str] = {}
        self.iv_slot: dict[tuple[str, str, str], str] = {}
        # 같이 보는 사람이 같은 시각에 두 팀에 앉지 않게 — 값은 앉힌 팀 이름
        self.applicant_slot: dict[tuple[str, str, str], str] = {}
        self.iv_day: Counter = Counter()
        self.iv_total: Counter = Counter()
        self.day_count: Counter = Counter()
        self.day_grad: Counter = Counter()
        # 편중은 팀 안에서 생긴다 — 한 팀의 대학원생이 한 날에 몰리면 그 날 면접은
        # 저희끼리 비교하는 자리가 된다. 팀마다 면접일이 다르므로 회차 전체의
        # 날별 셈으로는 그것이 안 보인다. 규칙1과 같은 잣대로 재려고 함께 센다.
        self.team_day_count: Counter = Counter()
        self.team_day_grad: Counter = Counter()
        self.team_day_hours: dict[tuple[str, str], set[int]] = defaultdict(set)
        self.assignments: list[PlannedAssignment] = []

    # -- 조회 -------------------------------------------------------------
    def _daily_cap(self, iv: InterviewerIn) -> int:
        cap = iv.max_daily if iv.max_daily else self.max_daily_default
        return min(cap, MAX_SLOTS_PER_DAY)

    def team_slot_free(self, team: str, day: str, hour: str) -> bool:
        return (team, day, hour) not in self.team_slot

    def applicant_slot_free(self, applicant_id: str | None, day: str, hour: str) -> bool:
        """그 사람이 이 시각에 이미 다른 면접에 잡혀 있지 않은가.

        두 팀이 같이 보기로 한 사람만 해당한다 — 자리가 둘이니 시각은 달라야 한다.
        """
        return not applicant_id or (applicant_id, day, hour) not in self.applicant_slot

    def _adjacent(self, interviewer_id: str, day: str, hour: str) -> bool:
        """그 사람이 바로 앞 · 뒤 칸에 이미 면접이 있는가.

        점심 시간을 따로 두지 않으므로 하루 여덟 칸은 죽 이어진 한 덩어리다.
        칸 순서상 하나 앞 · 하나 뒤면 붙어 있는 것으로 본다.
        """
        if hour not in HOURS:
            return False
        index = HOURS.index(hour)
        return any(
            (interviewer_id, day, HOURS[near]) in self.iv_slot
            for near in (index - 1, index + 1)
            if 0 <= near < len(HOURS)
        )

    def _fits(self, iv: InterviewerIn, day: str, hour: str) -> bool:
        return (
            (self.ignore_availability or iv.is_available(day, hour))
            and (iv.interviewer_id, day, hour) not in self.iv_slot
            and self.iv_day[(iv.interviewer_id, day)] < self._daily_cap(iv)
        )

    def pinned_for(self, team: str, applicant_id: str | None) -> str | None:
        """이 자리(팀 × 지원자)에 부서가 정해 보낸 담당자 — 없으면 None."""
        if not applicant_id:
            return None
        mine = self.pinned_by_team.get(team)
        if mine is not None:
            return mine.get(applicant_id)
        return self.pinned.get(applicant_id)

    def find_interviewer(
        self, team: str, day: str, hour: str, applicant_id: str | None = None
    ) -> InterviewerIn | None:
        """해당 슬롯을 맡을 수 있는 면접관 중 붙여 앉힐 수 있는 사람을 먼저.

        부서가 짝을 정해 보낸 지원자는 담당자를 고르지 않는다. 정해진 그 사람이
        이 시간에 안 되면 다른 사람을 대신 세우는 게 아니라 그냥 이 시간을
        포기한다 — 부서의 결정을 시간표가 뒤집지 않게 하려는 것이다.

        면접관 입장에서는 09시 한 건 보고 세 시간 비웠다가 15시에 또 한 건 보는
        것보다 연달아 보는 편이 낫다. 그래서 앞 · 뒤 시간에 이미 면접이 있는
        사람을 먼저 고르고, 그런 사람이 없을 때만 지금까지 가장 적게 맡은
        사람에게 넘긴다. 하루 한도(가능 시간에서 정해진 칸 수)는 그대로 지키므로
        한 사람이 온종일 떠안지는 않는다.

        priority=1(리더)을 뒤로 미뤄 v1에서 관측된 '리더 90% 부하'를 방지한다.
        """
        pinned_id = self.pinned_for(team, applicant_id)
        if pinned_id is not None:
            iv = self.by_id.get(pinned_id)
            # 남의 팀 담당자면 이 자리는 비운다. 두 팀이 같이 보는 사람은 팀별
            # 짝이 없으면 한쪽 팀 담당자로 뭉개지는데, 그 사람을 상대 팀
            # 면접장에 세우면 팀 명단과 시간표가 어긋난다.
            if iv is None or iv.team != team or not self._fits(iv, day, hour):
                return None
            return iv

        candidates = [iv for iv in self.by_team.get(team, []) if self._fits(iv, day, hour)]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda iv: (
                not self._adjacent(iv.interviewer_id, day, hour),
                iv.priority == 1,
                self.iv_total[iv.interviewer_id],
                self.iv_day[(iv.interviewer_id, day)],
                iv.interviewer_id,
            ),
        )

    def seat_for(self, team: str, applicant_id: str | None) -> tuple[int, int] | None:
        """부서가 이 사람에게 잡아 준 자리 (몇 일차, 몇 번째 칸) — 없으면 None."""
        if not applicant_id:
            return None
        return self.seats.get(team, {}).get(applicant_id)

    def seat_miss_reason(self, team: str, applicant_id: str | None,
                         day: str, hour: str) -> str:
        """부서가 잡아 준 자리에 왜 못 앉혔는지 한 가지로 답한다.

        옮긴 것 자체보다 왜 옮겼는지가 부서에게는 중요하다 — '담당자가 그 시간에
        안 되신다' 와 '그 자리를 다른 분이 먼저 쓰셨다' 는 대응이 다르다.
        """
        if not self.team_slot_free(team, day, hour):
            return "SEAT_MOVED_TAKEN"
        if not self.applicant_slot_free(applicant_id, day, hour):
            return "SEAT_MOVED_TAKEN"
        iv = self.by_id.get(self.pinned_for(team, applicant_id) or "")
        if iv is None or iv.team != team:
            return "SEAT_MOVED_OWNER"
        if not (self.ignore_availability or iv.is_available(day, hour)):
            return "SEAT_MOVED_BAND"
        if (iv.interviewer_id, day, hour) in self.iv_slot:
            return "SEAT_MOVED_BUSY"
        if self.iv_day[(iv.interviewer_id, day)] >= self._daily_cap(iv):
            return "SEAT_MOVED_CAP"
        return "SEAT_MOVED_OWNER"

    def can_place(self, team: str, day: str, hour: str, applicant_id: str | None = None) -> bool:
        return (
            self.team_slot_free(team, day, hour)
            and self.applicant_slot_free(applicant_id, day, hour)
            and self.find_interviewer(team, day, hour, applicant_id) is not None
        )

    # -- 배치 -------------------------------------------------------------
    def place(
        self, applicant: ApplicantIn, day: str, hour: str, tags: list[str] | None = None
    ) -> PlannedAssignment | None:
        if day not in DAYS or hour not in HOURS:
            return None
        if not self.team_slot_free(applicant.team, day, hour):
            return None
        if not self.applicant_slot_free(applicant.applicant_id, day, hour):
            return None
        iv = self.find_interviewer(applicant.team, day, hour, applicant.applicant_id)
        if iv is None:
            return None

        reasons = list(tags or applicant.tags or ["PRIMARY_JOB"])
        # 부서 자리에 못 앉아 여기로 온 사람은 그 까닭을 자리에 함께 적어 둔다
        moved = self.seat_miss.get(applicant.applicant_id)
        if moved and moved not in reasons:
            reasons.append(moved)
        assignment = PlannedAssignment(
            applicant_id=applicant.applicant_id,
            applicant_name=applicant.name,
            interviewer_id=iv.interviewer_id,
            team=applicant.team,
            degree=applicant.degree,
            day=day,
            hour=hour,
            reason_tags=reasons,
        )
        self.team_slot[(applicant.team, day, hour)] = applicant.applicant_id
        self.iv_slot[(iv.interviewer_id, day, hour)] = applicant.applicant_id
        self.applicant_slot[(applicant.applicant_id, day, hour)] = applicant.team
        self.iv_day[(iv.interviewer_id, day)] += 1
        self.iv_total[iv.interviewer_id] += 1
        self.day_count[day] += 1
        self.team_day_count[(applicant.team, day)] += 1
        if applicant.is_grad:
            self.day_grad[day] += 1
            self.team_day_grad[(applicant.team, day)] += 1
        self.team_day_hours[(applicant.team, day)].add(HOURS.index(hour))
        self.assignments.append(assignment)
        return assignment

    # -- 지표 보조 ---------------------------------------------------------
    def day_grad_ratio(self, day: str) -> float:
        total = self.day_count[day]
        return (self.day_grad[day] / total) if total else 0.0

    def would_break_contiguity(self, team: str, day: str, hour: str) -> bool:
        """해당 슬롯에 넣었을 때 팀-면접일 세로 연속(규칙3)이 깨지는가"""
        used = self.team_day_hours.get((team, day), set())
        if not used:
            return False
        candidate = used | {HOURS.index(hour)}
        return (max(candidate) - min(candidate)) != (len(candidate) - 1)
