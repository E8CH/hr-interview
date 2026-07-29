"""하드 제약 재검증 · 소프트 페널티 계산 (v3.1)

재편성에서 **모든** 후보 슬롯은 배정 직전 이 모듈로 재검증된다.
v3 에서는 재검증이 없어 하드 위반이 발생했다 — v3.1 의 핵심 수정점.

하드 제약
  H1 (규칙2)  같은 팀 · 같은 (요일,시간) 중복 금지
  H2          면접위원 동시간 이중 예약 금지
  H3          지원자 동시간 이중 예약 금지
  H4          면접위원 일일 최대 면접 수(max_daily) 초과 금지

소프트 규칙 (페널티만 부여, 배정을 막지 않음)
  S1 (규칙1)  요일별 대학원 비율 30% ±20%p
  S3 (규칙3)  동일 팀 세로(같은 요일 연속 시간) 배치
  S4 (규칙4)  첫 타임(09시·14시)은 소규모 조 우선
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from shared.contracts.constants import HOURS

from app.domain.schedule import (ApplicantInfo, InterviewerInfo,
                                 ScheduleAssignment)

#: 연속 배치 판정을 위한 시간 블록 (점심 시간으로 오전/오후가 끊긴다)
HOUR_BLOCKS: list[list[str]] = [["09시", "10시", "11시"], ["14시", "15시", "16시"]]
FIRST_HOURS: set[str] = {"09시", "14시"}

HOUR_INDEX: dict[str, int] = {h: i for i, h in enumerate(HOURS)}

GRAD_TARGET = 0.30
GRAD_TOLERANCE = 0.20

# 소프트 페널티 가중치
W_GRAD_BALANCE = 20      # 허용 범위를 벗어난 비율 1.0 당
W_VERTICAL_GAP = 3       # 팀-요일 단위 추가 블록 1개 당
W_FIRST_SLOT = 2         # 대형 조가 첫 타임을 차지한 건수 당


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
    per_day: dict[str, list[str]] = defaultdict(list)
    for a in assignments:
        ap = applicants.get(a.applicant_id)
        per_day[a.day].append(ap.degree_type if ap else "학사")

    penalty = 0.0
    for _day, degrees in per_day.items():
        if not degrees:
            continue
        ratio = sum(1 for d in degrees if d == "대학원") / len(degrees)
        excess = abs(ratio - GRAD_TARGET) - GRAD_TOLERANCE
        if excess > 0:
            penalty += excess * W_GRAD_BALANCE
    return int(round(penalty))


def _vertical_group_penalty(assignments: list[ScheduleAssignment],
                            interviewers: dict[str, InterviewerInfo]) -> int:
    """같은 팀이 같은 요일에 흩어져 있을수록 Webex 재입장이 늘어난다."""
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
                        interviewers: dict[str, InterviewerInfo]) -> int:
    """첫 타임은 소규모 조 우선 — 대형 조가 차지하면 페널티."""
    team_size: dict[str, int] = defaultdict(int)
    for a in assignments:
        iv = interviewers.get(a.interviewer_id)
        team_size[iv.team if iv else a.team] += 1
    if not team_size:
        return 0
    avg = sum(team_size.values()) / len(team_size)

    penalty = 0
    for a in assignments:
        if a.hour not in FIRST_HOURS:
            continue
        iv = interviewers.get(a.interviewer_id)
        team = iv.team if iv else a.team
        if team_size[team] > avg:
            penalty += W_FIRST_SLOT
    return penalty


def compute_soft_penalty(assignments: list[ScheduleAssignment],
                         interviewers: list[InterviewerInfo],
                         applicants: list[ApplicantInfo]) -> int:
    iv_map = {iv.interviewer_id: iv for iv in interviewers}
    ap_map = {ap.applicant_id: ap for ap in applicants}
    return (_grad_balance_penalty(assignments, ap_map)
            + _vertical_group_penalty(assignments, iv_map)
            + _first_slot_penalty(assignments, iv_map))


def soft_penalty_breakdown(assignments: list[ScheduleAssignment],
                           interviewers: list[InterviewerInfo],
                           applicants: list[ApplicantInfo]) -> dict[str, int]:
    iv_map = {iv.interviewer_id: iv for iv in interviewers}
    ap_map = {ap.applicant_id: ap for ap in applicants}
    return {
        "RULE1_GRAD_BALANCE": _grad_balance_penalty(assignments, ap_map),
        "RULE3_VERTICAL_GROUP": _vertical_group_penalty(assignments, iv_map),
        "RULE4_FIRST_SLOT": _first_slot_penalty(assignments, iv_map),
    }
