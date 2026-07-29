"""Service 04 (Scheduler) 클라이언트

USE_MOCK=true (PoC 기본) 이면 결정적 mock 시간표를 생성한다.
결정적이어야 테스트·성능 측정이 재현 가능하다 — 난수를 쓰지 않는다.
"""
from __future__ import annotations

import logging
from datetime import datetime

from shared.contracts.constants import DAYS, HOURS

from app.config import settings
from app.domain.repair_plan import SlotRef
from app.domain.schedule import (ApplicantInfo, InterviewerInfo,
                                 ScheduleAssignment, ScheduleSnapshot)

log = logging.getLogger("repair-engine.scheduler")

TEAMS = ["AI솔루션팀", "로봇응용기술팀", "미래혁신팀", "배터리기술팀", "전극기술팀"]

INTERVIEWERS_PER_TEAM = 2
ASSIGNMENTS_PER_TEAM = 13
MAX_DAILY = 6
RESERVE_BASE = 3          # 팀별 예비 슬롯 = RESERVE_BASE + 팀 인덱스

#: mock 스냅샷을 완전히 결정적으로 만들기 위한 고정 생성 시각
MOCK_CREATED_AT = datetime(2026, 7, 1, 9, 0, 0)


def _slot_combos() -> list[tuple[str, str]]:
    return [(d, h) for d in DAYS for h in HOURS]


def _lock_level_for(index: int) -> str:
    """결정적 락 분포 — LOCKED 20% · CONFIRMED 27% · 나머지 DRAFT"""
    if index % 5 == 0:
        return "LOCKED"
    if index % 3 == 0:
        return "CONFIRMED"
    return "DRAFT"


def build_mock_schedule(schedule_id: str, round_id: str,
                        lock_level: str = "CONFIRMED") -> ScheduleSnapshot:
    """5팀 × 13명 = 65 배정 + 팀별 불균등 예비 슬롯.

    예비 슬롯을 팀마다 1~5개로 다르게 두어 Plan A(팀 일치)와
    Plan C(cross-team)의 결과가 실제로 갈리도록 한다.
    """
    combos = _slot_combos()
    interviewers: list[InterviewerInfo] = []
    applicants: list[ApplicantInfo] = []
    assignments: list[ScheduleAssignment] = []
    reserved: list[SlotRef] = []

    seq = 0
    for t_i, team in enumerate(TEAMS):
        team_ivs = []
        for k in range(INTERVIEWERS_PER_TEAM):
            iv_id = f"IV{t_i * INTERVIEWERS_PER_TEAM + k + 1:03d}"
            team_ivs.append(iv_id)
            interviewers.append(InterviewerInfo(
                interviewer_id=iv_id, name=f"면접위원{iv_id}", team=team,
                max_daily=MAX_DAILY, priority=1 if k == 0 else 2))

        # 팀마다 시작 지점을 어긋나게 해 동일 시간대 쏠림을 피한다
        rotated = combos[t_i * 3:] + combos[: t_i * 3]
        booked = rotated[:ASSIGNMENTS_PER_TEAM]
        reserve_count = RESERVE_BASE + t_i             # 팀별 3~7개 (불균등)
        free = rotated[ASSIGNMENTS_PER_TEAM:]

        for i, (day, hour) in enumerate(booked):
            seq += 1
            applicant_id = str(3300000 + seq)
            degree = "대학원" if seq % 3 == 0 else "학사"
            applicants.append(ApplicantInfo(
                applicant_id=applicant_id, name=f"지원자{seq:03d}",
                team_1st=team, degree_type=degree))
            iv_id = team_ivs[i % INTERVIEWERS_PER_TEAM]
            assignments.append(ScheduleAssignment(
                assignment_id=f"AS{seq:04d}",
                applicant_id=applicant_id,
                interviewer_id=iv_id,
                day=day, hour=hour, team=team,
                lock_level=_lock_level_for(seq),
                reason_tags=["PRIMARY_JOB"],
                created_at=MOCK_CREATED_AT))

        for j, (day, hour) in enumerate(free[:reserve_count]):
            iv_id = team_ivs[j % INTERVIEWERS_PER_TEAM]
            reserved.append(SlotRef(day=day, hour=hour,
                                    interviewer_id=iv_id, team=team))

    return ScheduleSnapshot(
        schedule_id=schedule_id, round_id=round_id, lock_level=lock_level,
        assignments=assignments, reserved_slots=reserved,
        interviewers=interviewers, applicants=applicants)


def _fetch_remote(schedule_id: str, round_id: str) -> ScheduleSnapshot:
    import httpx

    url = f"{settings.scheduler_base_url}/api/v1/schedule/{schedule_id}"
    resp = httpx.get(url, timeout=10.0)
    resp.raise_for_status()
    data = (resp.json() or {}).get("data") or {}
    data.setdefault("schedule_id", schedule_id)
    data.setdefault("round_id", round_id)
    return ScheduleSnapshot.model_validate(data)


def fetch_schedule(schedule_id: str, round_id: str,
                   lock_level: str = "CONFIRMED") -> ScheduleSnapshot:
    """Service 04 에서 시간표를 로드한다 (mock 모드면 합성 스냅샷)."""
    if settings.use_mock:
        return build_mock_schedule(schedule_id, round_id, lock_level)
    return _fetch_remote(schedule_id, round_id)
