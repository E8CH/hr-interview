"""Service 03 (Response Collector) 클라이언트

조직별 응답 통계 보강용. 07은 이벤트 프로젝션(org_response_stats)을 1차 소스로
쓰고, 이 클라이언트는 이벤트에 org 정보가 없을 때의 보조 수단이다.
USE_MOCK=true(PoC)면 빈 결과를 돌려주어 프로젝션 값만 사용한다.
"""
from __future__ import annotations

import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)


class ResponseClient:
    def __init__(self, base_url: str | None = None, use_mock: bool | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.response_collector_url).rstrip("/")
        self.use_mock = settings.use_mock if use_mock is None else use_mock
        self.timeout = settings.http_timeout_s

    def get_org_stats(self, round_id: str) -> list[dict]:
        """[{"org":..., "mean_hours":..., "completion_rate":...}, ...]"""
        if self.use_mock:
            return []

        import httpx

        url = f"{self.base_url}/api/v1/responses/stats"
        try:
            response = httpx.get(url, params={"round_id": round_id}, timeout=self.timeout)
            response.raise_for_status()
            data = (response.json() or {}).get("data") or []
            return data if isinstance(data, list) else []
        except Exception as exc:
            log.warning(
                "response_client.unavailable", round_id=round_id, error=str(exc)
            )
            return []
