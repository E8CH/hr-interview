"""APScheduler — 30분 주기 리마인더 폴링

명세: 리마인더 스케줄 정확도 ±5분 → 폴링 주기 30분보다 촘촘한 5분 그리드로 돌리되,
판정은 `should_send_reminder` 가 하므로 중복 발송은 발생하지 않는다.
"""
from __future__ import annotations

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.infrastructure.db import session_scope
from app.services import reminder_service

logger = structlog.get_logger(__name__)

JOB_ID = "reminder-cycle"

_scheduler: BackgroundScheduler | None = None


def reminder_job() -> int:
    """스케줄러 잡 본체 — 발송 건수 반환."""
    try:
        with session_scope() as db:
            sent = reminder_service.run_reminder_cycle(db)
        return len(sent)
    except Exception as exc:  # 잡이 죽어 스케줄러가 멈추지 않도록
        logger.error("reminder_job_failed", error=str(exc))
        return 0


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.enable_scheduler:
        logger.info("scheduler_disabled")
        return None
    if _scheduler is not None:
        return _scheduler

    trigger = (
        IntervalTrigger(seconds=settings.reminder_poll_seconds)
        if settings.reminder_poll_seconds > 0
        else IntervalTrigger(minutes=settings.reminder_poll_minutes)
    )

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        reminder_job,
        trigger=trigger,
        id=JOB_ID,
        name="3단계 리마인더 폴링",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,  # ±5분
    )
    _scheduler.start()
    logger.info(
        "scheduler_started",
        interval_minutes=settings.reminder_poll_minutes,
        interval_seconds_override=settings.reminder_poll_seconds or None,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("scheduler_stopped")


def get_scheduler() -> BackgroundScheduler | None:
    return _scheduler
