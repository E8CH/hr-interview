"""shared/contracts 브리지 (읽기 전용 공통 계약을 import 가능하게 함)

`shared/contracts`는 레포 루트에 있으므로 sys.path에 루트를 넣어 재노출한다.
이 모듈만 통해 공통 계약을 사용하고, 원본 파일은 절대 수정하지 않는다.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.contracts.constants import (  # noqa: E402
    BACK_HOURS,
    BACK_START_MINUTES,
    BAND_ALL,
    BAND_BACK,
    BAND_FRONT,
    BAND_NONE,
    DAYS,
    DAYS_PER_TEAM,
    DEFAULT_TIMING,
    FRONT_END_MINUTES,
    FRONT_HOURS,
    HOURS,
    NOON_MINUTES,
    RULES,
    TAGS,
    TIME_BANDS,
    band_availability,
    band_hours,
    band_name,
    band_of,
    band_slots,
    day_blocks,
    first_slots,
    hour_bands,
    hour_spans,
    normalize_availability,
    plan_team_days,
    slot_spans,
)
from shared.contracts.events import (  # noqa: E402
    EventEnvelope,
    EventType,
    ScheduleGeneratedPayload,
    ScheduleLockedPayload,
)
from shared.contracts.types import Applicant, Assignment, Interviewer  # noqa: E402

__all__ = [
    "DAYS",
    "HOURS",
    "RULES",
    "TAGS",
    "FRONT_HOURS",
    "BACK_HOURS",
    "TIME_BANDS",
    "BAND_ALL",
    "BAND_FRONT",
    "BAND_BACK",
    "BAND_NONE",
    "DEFAULT_TIMING",
    "FRONT_END_MINUTES",
    "BACK_START_MINUTES",
    "NOON_MINUTES",
    "band_availability",
    "band_hours",
    "band_name",
    "band_of",
    "band_slots",
    "day_blocks",
    "first_slots",
    "hour_bands",
    "hour_spans",
    "normalize_availability",
    "plan_team_days",
    "slot_spans",
    "DAYS_PER_TEAM",
    "EventEnvelope",
    "EventType",
    "ScheduleGeneratedPayload",
    "ScheduleLockedPayload",
    "Applicant",
    "Assignment",
    "Interviewer",
    "REPO_ROOT",
]

REPO_ROOT = _REPO_ROOT
