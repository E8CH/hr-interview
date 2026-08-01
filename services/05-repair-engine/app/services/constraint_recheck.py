"""하드 제약 재검증 · 소프트 페널티 계산 (v3.1)

재편성에서 **모든** 후보 슬롯은 배정 직전 이 모듈로 재검증된다.
v3 에서는 재검증이 없어 하드 위반이 발생했다 — v3.1 의 핵심 수정점.

하드 제약
  H1 (규칙2)  같은 팀 · 같은 (날,시간) 중복 금지
  H2          면접위원 동시간 이중 예약 금지
  H3          지원자 동시간 이중 예약 금지
  H4          면접위원 일일 최대 면접 수(max_daily) 초과 금지

소프트 규칙 (페널티만 부여, 배정을 막지 않음)
  S1 (규칙1)  날별 대학원 비율이 그 회차 전체의 비율 ±20%p
  S3 (규칙3)  동일 팀 세로(같은 날 연속 시간) 배치
  S4 (규칙4)  오전 · 오후 첫 타임은 그날 적게 보는 조 우선
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from shared.contracts.constants import HOURS, day_blocks

from app.domain.schedule import (ApplicantInfo, InterviewerInfo,
                                 ScheduleAssignment)

#: 연속 배치 판정을 위한 시간 블록 — 점심 시간을 따로 두지 않으므로 하루가 한 덩어리다.
#: 오전 · 오후로 가르는 `day_blocks()` 와는 일부러 다르다. 세로 연속은 방을 몇 번
#: 다시 여는지의 문제라 12시를 넘겨 이어 앉는 것은 끊어 앉는 것이 아니다.
HOUR_BLOCKS: list[list[str]] = [list(HOURS)]

HOUR_INDEX: dict[str, int] = {h: i for i, h in enumerate(HOURS)}

#: 하루 대학원 비율의 허용폭. 목표값 자체는 못 박지 않는다 — 그 회차 명단의
#: 실제 비율이 목표다. 04 규칙1도 같은 잣대로 매긴다. 3할 같은 고정값을 쓰면
#: 대학원생이 1할인 회차는 고르게 나눠도 온 날이 벌점이라, 재편성이 줄일 수
#: 없는 벌점을 줄이겠다고 멀쩡한 자리를 흔든다.
GRAD_TOLERANCE = 0.20

# 소프트 페널티 가중치
W_GRAD_BALANCE = 20      # 허용 범위를 벗어난 비율 1.0 당
W_VERTICAL_GAP = 3       # 팀-날 단위 추가 블록 1개 당
W_FIRST_SLOT = 2         # 큰 조가 첫 타임을 차지한 덩어리(날 × 오전/오후) 1개 당


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "detail": self.detail}


@dataclass
class ConstraintIndex:
    """증분 검증용 인덱스.

    후보 슬롯마다 전체 스캔을 하지 않도록 카운터를 유지한다.
    최종 Plan 은 `check_hard_constraints()` 전수 검사로 다시 확인한다.
    """
    interviewers: dict[str, InterviewerInfo]
    team_slot: dict[tuple[str, str, str], int] = field(default_factory=lambda: defaultdict(int))
    iv_slot: dict[tuple[str, str, str], int] = field(default_factory=lambda: defaultdict(int))
    ap_slot: dict[tuple[str, str, str], int] = field(default_factory=lambda: defaultdict(int))
    iv_day: dict[tuple[str, str], int] = field(default_factory=lambda: defaultdict(int))
    ap_total: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @classmethod
    def build(cls, assignments: list[ScheduleAssignment],
              interviewers: list[InterviewerInfo]) -> "ConstraintIndex":
        idx = cls(interviewers={iv.interviewer_id: iv for iv in interviewers})
        for a in assignments:
            idx.add(a)
        return idx

    def _team_of(self, assignment: ScheduleAssignment) -> str:
        iv = self.interviewers.get(assignment.interviewer_id)
        return iv.team if iv else assignment.team

    def add(self, a: ScheduleAssignment) -> None:
        team = self._team_of(a)
        self.team_slot[(a.day, a.hour, team)] += 1
        self.iv_slot[(a.day, a.hour, a.interviewer_id)] += 1
        self.ap_slot[(a.day, a.hour, a.applicant_id)] += 1
        self.iv_day[(a.interviewer_id, a.day)] += 1
        self.ap_total[a.applicant_id] += 1

    def remove(self, a: ScheduleAssignment) -> None:
        team = self._team_of(a)
        for store, key in (
            (self.team_slot, (a.day, a.hour, team)),
            (self.iv_slot, (a.day, a.hour, a.interviewer_id)),
            (self.ap_slot, (a.day, a.hour, a.applicant_id)),
            (self.iv_day, (a.interviewer_id, a.day)),
        ):
            store[key] = max(0, store.get(key, 0) - 1)
        self.ap_total[a.applicant_id] = max(0, self.ap_total.get(a.applicant_id, 0) - 1)

    def blocking_reasons(self, applicant_id: str, day: str, hour: str,
                         interviewer_id: str, team: str) -> list[str]:
        """이 슬롯에 지원자를 넣으면 위반되는 하드 제약 목록 (비어 있으면 안전)"""
        reasons: list[str] = []
        if self.team_slot.get((day, hour, team), 0) > 0:
            reasons.append("H1_TEAM_CONFLICT")
        if self.iv_slot.get((day, hour, interviewer_id), 0) > 0:
            reasons.append("H2_INTERVIEWER_DOUBLE_BOOK")
        if self.ap_slot.get((day, hour, applicant_id), 0) > 0:
            reasons.append("H3_APPLICANT_DOUBLE_BOOK")
        iv = self.interviewers.get(interviewer_id)
        max_daily = iv.max_daily if iv else 6
        if self.iv_day.get((interviewer_id, day), 0) >= max_daily:
            reasons.append("H4_MAX_DAILY_EXCEEDED")
        return reasons

    def is_safe(self, applicant_id: str, day: str, hour: str,
                interviewer_id: str, team: str) -> bool:
        return not self.blocking_reasons(applicant_id, day, hour, interviewer_id, team)


def check_hard_constraints(assignments: list[ScheduleAssignment],
                           interviewers: list[InterviewerInfo]) -> list[Violation]:
    """배정 전체에 대한 하드 제약 전수 검사."""
    iv_map = {iv.interviewer_id: iv for iv in interviewers}

    def team_of(a: ScheduleAssignment) -> str:
        iv = iv_map.get(a.interviewer_id)
        return iv.team if iv else a.team

    team_slot: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    iv_slot: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    ap_slot: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    iv_day: dict[tuple[str, str], int] = defaultdict(int)

    for a in assignments:
        team_slot[(a.day, a.hour, team_of(a))].append(a.applicant_id)
        iv_slot[(a.day, a.hour, a.interviewer_id)].append(a.applicant_id)
        ap_slot[(a.day, a.hour, a.applicant_id)].append(a.assignment_id)
        iv_day[(a.interviewer_id, a.day)] += 1

    violations: list[Violation] = []
    for (day, hour, team), members in team_slot.items():
        if len(members) > 1:
            violations.append(Violation(
                "RULE2_TEAM_CONFLICT", "HARD",
                f"{team} {day} {hour} 동시 {len(members)}건: {members}"))
    for (day, hour, iv_id), members in iv_slot.items():
        if len(members) > 1:
            violations.append(Violation(
                "H2_INTERVIEWER_DOUBLE_BOOK", "HARD",
                f"{iv_id} {day} {hour} 동시 {len(members)}건"))
    for (day, hour, ap_id), rows in ap_slot.items():
        if len(rows) > 1:
            violations.append(Violation(
                "H3_APPLICANT_DOUBLE_BOOK", "HARD",
                f"지원자 {ap_id} {day} {hour} 중복 {len(rows)}건"))
    for (iv_id, day), count in iv_day.items():
        limit = iv_map[iv_id].max_daily if iv_id in iv_map else 6
        if count > limit:
            violations.append(Violation(
                "H4_MAX_DAILY_EXCEEDED", "HARD",
                f"{iv_id} {day} {count}건 > 최대 {limit}건"))
    return violations


def _grad_balance_penalty(assignments: list[ScheduleAssignment],
                          applicants: dict[str, ApplicantInfo]) -> int:
    """날마다의 대학원 비율이 **이 회차 전체의 비율** 에서 벗어난 만큼 벌한다.

    재려는 것은 "학사 · 석박사 편중이 생기지 않도록 요일을 분산" 했는가다.
    회차가 가진 비율대로 날마다 나뉘었으면 편중이 없는 것이다.
    """
    per_day: dict[str, list[str]] = defaultdict(list)
    for a in assignments:
        ap = applicants.get(a.applicant_id)
        per_day[a.day].append(ap.degree_type if ap else "학사")

    total = sum(len(d) for d in per_day.values())
    if not total:
        return 0
    target = sum(1 for degrees in per_day.values()
                 for d in degrees if d == "대학원") / total

    penalty = 0.0
    for _day, degrees in per_day.items():
        if not degrees:
            continue
        ratio = sum(1 for d in degrees if d == "대학원") / len(degrees)
        excess = abs(ratio - target) - GRAD_TOLERANCE
        if excess > 0:
            penalty += excess * W_GRAD_BALANCE
    return int(round(penalty))


def _vertical_group_penalty(assignments: list[ScheduleAssignment],
                            interviewers: dict[str, InterviewerInfo]) -> int:
    """같은 팀이 같은 날에 흩어져 있을수록 Webex 재입장이 늘어난다."""
    per_team_day: dict[tuple[str, str], set[str]] = defaultdict(set)
    for a in assignments:
        iv = interviewers.get(a.interviewer_id)
        team = iv.team if iv else a.team
        per_team_day[(team, a.day)].add(a.hour)

    penalty = 0
    for _key, hours in per_team_day.items():
        blocks = 0
        for block in HOUR_BLOCKS:
            present = [h in hours for h in block]
            prev = False
            for flag in present:
                if flag and not prev:
                    blocks += 1
                prev = flag
        if blocks > 1:
            penalty += (blocks - 1) * W_VERTICAL_GAP
    return penalty


def _first_slot_penalty(assignments: list[ScheduleAssignment],
                        interviewers: dict[str, InterviewerInfo],
                        timing: dict | None = None) -> int:
    """첫 타임은 수요 적은 조 우선 — 그날 크게 잡은 조가 차지하면 페널티.

    조의 크기는 **그날 그 조가 보는 인원** 으로 잰다. 회차 전체 건수가 아니다 —
    첫 타임 지각의 손해는 그 조의 그날 세로 줄이 얼마나 남았는지에 비례하고,
    04 규칙4도 같은 잣대로 매긴다. 잣대가 갈리면 04가 만점을 준 시간표를 05가
    벌점으로 깎아, 재편성이 멀쩡한 자리를 흔든다.

    오전 첫 타임과 오후 첫 타임을 모두 본다. 어느 칸이 그 자리인지는 면접 진행
    조건이 정한다 — 30분 면접이면 1타임과 7타임(12:30), 1시간 면접이면 1타임과
    4타임(12:30)이다. 그래서 칸 번호로 굳혀 두지 않고 그때그때 계산한다.
    """
    blocks = day_blocks(timing)

    def team_of(a: ScheduleAssignment) -> str:
        iv = interviewers.get(a.interviewer_id)
        return iv.team if iv else a.team

    day_team_size: dict[tuple[str, str], int] = defaultdict(int)
    in_block: dict[tuple[str, int], set[str]] = defaultdict(set)
    at_first: dict[tuple[str, int], set[str]] = defaultdict(set)
    for a in assignments:
        team = team_of(a)
        day_team_size[(a.day, team)] += 1
        for index, block in enumerate(blocks):
            if a.hour in block:
                in_block[(a.day, index)].add(team)
                if a.hour == block[0]:
                    at_first[(a.day, index)].add(team)

    def mean_size(day: str, teams: set[str]) -> float:
        sizes = [day_team_size[(day, team)] for team in teams]
        return sum(sizes) / len(sizes) if sizes else 0.0

    # 첫 칸에 앉은 조들이 그 덩어리의 조들보다 크면 벌점. 조 하나하나에 매기지
    # 않는 것은, 하루를 통째로 쓰는 조는 첫 칸을 피할 길이 없기 때문이다 — 그건
    # 그 조의 잘못이 아니라 인원의 문제다. 작은 조를 제쳐 두고 앉았을 때만 잡힌다.
    penalty = 0
    for key, seated in at_first.items():
        if not seated:
            continue
        if mean_size(key[0], seated) > mean_size(key[0], in_block[key]):
            penalty += W_FIRST_SLOT
    return penalty


def compute_soft_penalty(assignments: list[ScheduleAssignment],
                         interviewers: list[InterviewerInfo],
                         applicants: list[ApplicantInfo],
                         timing: dict | None = None) -> int:
    """`timing` 은 그 시간표를 만들 때 쓴 면접 진행 조건이다. 규칙4가 지키는
    '첫 타임' 이 몇 번째 칸인지가 이 값으로 갈리므로, 안 넘기면 재편성이 원래
    시간표와 다른 칸을 지키려 든다."""
    iv_map = {iv.interviewer_id: iv for iv in interviewers}
    ap_map = {ap.applicant_id: ap for ap in applicants}
    return (_grad_balance_penalty(assignments, ap_map)
            + _vertical_group_penalty(assignments, iv_map)
            + _first_slot_penalty(assignments, iv_map, timing))


def soft_penalty_breakdown(assignments: list[ScheduleAssignment],
                           interviewers: list[InterviewerInfo],
                           applicants: list[ApplicantInfo],
                           timing: dict | None = None) -> dict[str, int]:
    iv_map = {iv.interviewer_id: iv for iv in interviewers}
    ap_map = {ap.applicant_id: ap for ap in applicants}
    return {
        "RULE1_GRAD_BALANCE": _grad_balance_penalty(assignments, ap_map),
        "RULE3_VERTICAL_GROUP": _vertical_group_penalty(assignments, iv_map),
        "RULE4_FIRST_SLOT": _first_slot_penalty(assignments, iv_map, timing),
    }
