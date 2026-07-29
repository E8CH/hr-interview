"""배치 보드 — 하드 제약을 구조적으로 위반할 수 없게 만드는 공용 엔진

모든 알고리즘(v1/v4/v5)은 Board.place()만 통해 배치한다.
place()는 아래를 모두 만족할 때만 성공하므로 하드 위반이 0으로 유지된다.
  1) 규칙2(HARD): 같은 팀 동시간 중복 금지 → (team, day, hour) 유일
  2) 면접관 시간 충돌 금지 → (interviewer, day, hour) 유일
  3) 일일 타임 한도 초과 금지 → min(max_daily, MAX_SLOTS_PER_DAY)
  4) 면접관 가용성 밖 배정 금지
"""
from __future__ import annotations

from collections import Counter, defaultdict

from app.domain.schemas import ApplicantIn, InterviewerIn, PlannedAssignment
from app.infrastructure.contracts import AM_HOURS, DAYS, HOURS, PM_HOURS

MAX_SLOTS_PER_DAY = 8  # 명세의 "8타임 초과" 하드 상한


class Board:
    def __init__(self, interviewers: list[InterviewerIn], max_daily_default: int = 6) -> None:
        self.max_daily_default = max_daily_default
        self.by_team: dict[str, list[InterviewerIn]] = defaultdict(list)
        for iv in interviewers:
            self.by_team[iv.team].append(iv)

        self.team_slot: dict[tuple[str, str, str], str] = {}
        self.iv_slot: dict[tuple[str, str, str], str] = {}
        self.iv_day: Counter = Counter()
        self.iv_total: Counter = Counter()
        self.day_count: Counter = Counter()
        self.day_grad: Counter = Counter()
        self.team_day_hours: dict[tuple[str, str], set[int]] = defaultdict(set)
        self.assignments: list[PlannedAssignment] = []

    # -- 조회 -------------------------------------------------------------
    def _daily_cap(self, iv: InterviewerIn) -> int:
        cap = iv.max_daily if iv.max_daily else self.max_daily_default
        return min(cap, MAX_SLOTS_PER_DAY)

    def team_slot_free(self, team: str, day: str, hour: str) -> bool:
        return (team, day, hour) not in self.team_slot

    def _adjacent(self, interviewer_id: str, day: str, hour: str) -> bool:
        """그 사람이 바로 앞 · 뒤 시간에 이미 면접이 있는가.

        11시와 14시는 점심시간을 사이에 두므로 붙어 있다고 보지 않는다 —
        오전은 오전끼리, 오후는 오후끼리만 이어 붙인다.
        """
        band = AM_HOURS if hour in AM_HOURS else PM_HOURS
        index = band.index(hour)
        return any(
            (interviewer_id, day, band[near]) in self.iv_slot
            for near in (index - 1, index + 1)
            if 0 <= near < len(band)
        )

    def find_interviewer(self, team: str, day: str, hour: str) -> InterviewerIn | None:
        """해당 슬롯을 맡을 수 있는 면접관 중 붙여 앉힐 수 있는 사람을 먼저.

        면접관 입장에서는 09시 한 건 보고 세 시간 비웠다가 15시에 또 한 건 보는
        것보다 연달아 보는 편이 낫다. 그래서 앞 · 뒤 시간에 이미 면접이 있는
        사람을 먼저 고르고, 그런 사람이 없을 때만 지금까지 가장 적게 맡은
        사람에게 넘긴다. 하루 한도(가능 시간에서 정해진 칸 수)는 그대로 지키므로
        한 사람이 온종일 떠안지는 않는다.

        priority=1(리더)을 뒤로 미뤄 v1에서 관측된 '리더 90% 부하'를 방지한다.
        """
        candidates = [
            iv
            for iv in self.by_team.get(team, [])
            if iv.is_available(day, hour)
            and (iv.interviewer_id, day, hour) not in self.iv_slot
            and self.iv_day[(iv.interviewer_id, day)] < self._daily_cap(iv)
        ]
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

    def can_place(self, team: str, day: str, hour: str) -> bool:
        return self.team_slot_free(team, day, hour) and self.find_interviewer(team, day, hour) is not None

    # -- 배치 -------------------------------------------------------------
    def place(
        self, applicant: ApplicantIn, day: str, hour: str, tags: list[str] | None = None
    ) -> PlannedAssignment | None:
        if day not in DAYS or hour not in HOURS:
            return None
        if not self.team_slot_free(applicant.team, day, hour):
            return None
        iv = self.find_interviewer(applicant.team, day, hour)
        if iv is None:
            return None

        assignment = PlannedAssignment(
            applicant_id=applicant.applicant_id,
            applicant_name=applicant.name,
            interviewer_id=iv.interviewer_id,
            team=applicant.team,
            degree=applicant.degree,
            day=day,
            hour=hour,
            reason_tags=list(tags or applicant.tags or ["PRIMARY_JOB"]),
        )
        self.team_slot[(applicant.team, day, hour)] = applicant.applicant_id
        self.iv_slot[(iv.interviewer_id, day, hour)] = applicant.applicant_id
        self.iv_day[(iv.interviewer_id, day)] += 1
        self.iv_total[iv.interviewer_id] += 1
        self.day_count[day] += 1
        if applicant.is_grad:
            self.day_grad[day] += 1
        self.team_day_hours[(applicant.team, day)].add(HOURS.index(hour))
        self.assignments.append(assignment)
        return assignment

    # -- 지표 보조 ---------------------------------------------------------
    def day_grad_ratio(self, day: str) -> float:
        total = self.day_count[day]
        return (self.day_grad[day] / total) if total else 0.0

    def would_break_contiguity(self, team: str, day: str, hour: str) -> bool:
        """해당 슬롯에 넣었을 때 팀-요일 세로 연속(규칙3)이 깨지는가"""
        used = self.team_day_hours.get((team, day), set())
        if not used:
            return False
        candidate = used | {HOURS.index(hour)}
        return (max(candidate) - min(candidate)) != (len(candidate) - 1)
