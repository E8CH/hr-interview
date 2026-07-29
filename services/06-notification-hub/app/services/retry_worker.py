"""재시도 백그라운드 워커

주기적으로 큐에서 발송 시각이 도래한 항목을 꺼내 발송한다.
동기 SQLAlchemy 세션을 쓰므로 asyncio.to_thread 로 스레드에 넘긴다.
"""
from __future__ import annotations

import asyncio

import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def run_once(limit: int = 50) -> dict[str, int]:
    """큐 1회 처리 (동기). 테스트에서 직접 호출 가능."""
    from app.infrastructure.db import get_session_factory
    from app.services.dispatcher import process_due

    session = get_session_factory()()
    try:
        return process_due(session, limit=limit)
    finally:
        session.close()


class RetryWorker:
    def __init__(self, interval: float | None = None, limit: int = 50) -> None:
        self.interval = interval if interval is not None else settings.worker_interval
        self.limit = limit
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.cycles = 0

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                result = await asyncio.to_thread(run_once, self.limit)
                self.cycles += 1
                if result["processed"]:
                    log.info("retry_worker_cycle", **result)
            except Exception as exc:  # pragma: no cover - 워커는 죽지 않아야 한다
                log.error("retry_worker_error", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="retry-worker")
        log.info("retry_worker_started", interval=self.interval)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # pragma: no cover
                self._task.cancel()
            self._task = None
            log.info("retry_worker_stopped", cycles=self.cycles)
