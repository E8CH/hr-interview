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

from shared.contracts.constants import DAYS, HOURS, RULES, TAGS  # noqa: E402
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
