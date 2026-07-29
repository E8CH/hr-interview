"""Service 04 (Scheduler) 클라이언트

리포트 생성 파이프라인 4단계 "규칙 준수율 조회"용.
USE_MOCK=true(PoC)면 네트워크 호출 없이 결정론적 mock을 돌려준다.
"""
from __future__ import annotations

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

# 4대 배치 규칙 (00_SHARED_CONTRACT.md §8)
RULE_KEYS = [
    "RULE1_GRAD_BALANCE",
    "RULE2_TEAM_CONFLICT",
    "RULE3_VERTICAL_GROUP",
    "RULE4_FIRST_SLOT",
]

MOCK_RULE_COMPLIANCE: dict[str, float] = {
    "RULE1_GRAD_BALANCE": 88.0,
    "RULE2_TEAM_CONFLICT": 100.0,
    "RULE3_VERTICAL_GROUP": 84.0,
    "RULE4_FIRST_SLOT": 90.0,
}


class SchedulerClient:
    def __init__(self, base_url: str | None = None, use_mock: bool | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.scheduler_url).rstrip("/")
        self.use_mock = settings.use_mock if use_mock is None else use_mock
        self.timeout = settings.http_timeout_s

    def get_rule_compliance(self, round_id: str) -> dict[str, float]:
        """규칙별 준수율(%) 조회. 실패 시 mock으로 폴백한다."""
        if self.use_mock:
            return dict(MOCK_RULE_COMPLIANCE)

        import httpx

        url = f"{self.base_url}/api/v1/schedules/{round_id}/compliance"
        try:
            response = httpx.get(url, timeout=self.timeout)
            response.raise_for_status()
            data = (response.json() or {}).get("data") or {}
            rules = data.get("rules") or data
            return {k: float(v) for k, v in rules.items() if _is_number(v)}
        except Exception as exc:
            log.warning(
                "scheduler_client.fallback_to_mock", round_id=round_id, error=str(exc)
            )
            return dict(MOCK_RULE_COMPLIANCE)


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
