"""면접위원 명단 조회 (Service 02 Distributor)

USE_MOCK=true 이면 로컬 `./storage/roster.json` 을 읽고, 파일이 없으면 공통 계약의
팀 목록으로 기본 명단을 생성한다.

roster.json 형식:
    [{"name":"이지훈","email":"iv1@lge.com","team":"AI솔루션팀",
      "org":"제1기술원","dept_leader_email":"lead@lge.com"}, ...]
"""
from __future__ import annotations

import json

import structlog

from app.config import settings
from app.schemas import InviteeIn
from shared.contracts.constants import SERVICE_PORTS

logger = structlog.get_logger(__name__)

ROSTER_FILE = "roster.json"

_DEFAULT_TEAMS = ["AI솔루션팀", "로봇응용기술팀", "미래혁신팀", "배터리기술팀", "전극기술팀"]
_DEFAULT_ORGS = ["제1기술원", "제2사업부", "제3기술원"]


def fetch_invitees(plan_id: str, round_id: str) -> list[InviteeIn]:
    """배포 계획에 해당하는 면접위원 명단."""
    if settings.use_mock:
        return _mock_roster()
    return _fetch_remote(plan_id, round_id)


def _mock_roster() -> list[InviteeIn]:
    path = settings.storage_dir / ROSTER_FILE
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        logger.info("roster_loaded_from_file", path=str(path), count=len(raw))
        return [InviteeIn(**item) for item in raw]

    roster = [
        InviteeIn(
            name=f"면접위원{idx + 1:02d}",
            email=f"iv{idx + 1:02d}@lge.com",
            team=_DEFAULT_TEAMS[idx % len(_DEFAULT_TEAMS)],
            org=_DEFAULT_ORGS[idx % len(_DEFAULT_ORGS)],
            dept_leader_email=f"lead-{_DEFAULT_TEAMS[idx % len(_DEFAULT_TEAMS)]}@lge.com",
        )
        for idx in range(15)
    ]
    logger.info("roster_generated_default", count=len(roster))
    return roster


def _fetch_remote(plan_id: str, round_id: str) -> list[InviteeIn]:  # pragma: no cover - 통합 시 사용
    import httpx

    base = f"http://127.0.0.1:{SERVICE_PORTS['distributor']}"
    try:
        resp = httpx.get(
            f"{base}/api/v1/plans/{plan_id}/interviewers",
            params={"round_id": round_id},
            timeout=5.0,
        )
        resp.raise_for_status()
        items = resp.json().get("data", [])
        return [InviteeIn(**item) for item in items]
    except Exception as exc:
        logger.error("roster_fetch_failed", plan_id=plan_id, error=str(exc))
        return []
