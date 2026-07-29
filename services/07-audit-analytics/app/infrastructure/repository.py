"""리포지토리 — DB 접근 캡슐화

EventRepository는 의도적으로 append/read만 노출한다 (append-only 보장).
UPDATE/DELETE 메서드는 존재하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.db import (
    EventLog,
    KpiSnapshot,
    OrgResponseStat,
    Report,
    to_naive_utc,
    utcnow,
)


class EventRepository:
    """append-only 이벤트 로그"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def exists(self, event_id: str) -> bool:
        stmt = select(EventLog.log_id).where(EventLog.event_id == event_id)
        return self.session.execute(stmt).first() is not None

    def append(
        self,
        *,
        event_id: str,
        event_type: str,
        timestamp: datetime,
        round_id: str | None,
        producer: str | None,
        correlation_id: str | None,
        payload: dict,
    ) -> EventLog | None:
        """이벤트 저장. 이미 존재하는 event_id면 None (멱등)."""
        if self.exists(event_id):
            return None
        row = EventLog(
            event_id=event_id,
            event_type=event_type,
            timestamp=to_naive_utc(timestamp),
            round_id=round_id,
            producer=producer,
            correlation_id=correlation_id,
            payload=payload or {},
            received_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def by_round(
        self, round_id: str, event_types: list[str] | None = None
    ) -> list[EventLog]:
        stmt = select(EventLog).where(EventLog.round_id == round_id)
        if event_types:
            stmt = stmt.where(EventLog.event_type.in_(event_types))
        stmt = stmt.order_by(EventLog.timestamp.asc(), EventLog.log_id.asc())
        return list(self.session.execute(stmt).scalars())

    def query(
        self,
        *,
        round_id: str | None = None,
        event_types: list[str] | None = None,
        actor: str | None = None,
        producer: str | None = None,
        correlation_id: str | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[EventLog]:
        stmt = select(EventLog)
        if round_id:
            stmt = stmt.where(EventLog.round_id == round_id)
        if event_types:
            stmt = stmt.where(EventLog.event_type.in_(event_types))
        if producer:
            stmt = stmt.where(EventLog.producer == producer)
        if correlation_id:
            stmt = stmt.where(EventLog.correlation_id == correlation_id)
        if time_from:
            stmt = stmt.where(EventLog.timestamp >= to_naive_utc(time_from))
        if time_to:
            stmt = stmt.where(EventLog.timestamp <= to_naive_utc(time_to))
        stmt = stmt.order_by(EventLog.timestamp.asc(), EventLog.log_id.asc())

        rows = list(self.session.execute(stmt).scalars())
        if actor:
            # actor는 payload 내부 키라 DB 레벨 필터가 불가 (JSONB 함수 미사용, SQLite 호환)
            rows = [r for r in rows if _payload_actor_matches(r.payload, actor)]
        return rows[offset : offset + limit]

    def first_timestamp(
        self, round_id: str, event_types: list[str]
    ) -> datetime | None:
        stmt = select(func.min(EventLog.timestamp)).where(
            EventLog.round_id == round_id, EventLog.event_type.in_(event_types)
        )
        return self.session.execute(stmt).scalar()

    def last_timestamp(self, round_id: str, event_types: list[str]) -> datetime | None:
        stmt = select(func.max(EventLog.timestamp)).where(
            EventLog.round_id == round_id, EventLog.event_type.in_(event_types)
        )
        return self.session.execute(stmt).scalar()

    def last_before(
        self, round_id: str, event_types: list[str], moment: datetime
    ) -> EventLog | None:
        """moment 이전(포함)의 마지막 해당 타입 이벤트"""
        stmt = (
            select(EventLog)
            .where(
                EventLog.round_id == round_id,
                EventLog.event_type.in_(event_types),
                EventLog.timestamp <= to_naive_utc(moment),
            )
            .order_by(EventLog.timestamp.desc(), EventLog.log_id.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def count_by_type(self, round_id: str) -> dict[str, int]:
        stmt = (
            select(EventLog.event_type, func.count())
            .where(EventLog.round_id == round_id)
            .group_by(EventLog.event_type)
        )
        return {row[0]: row[1] for row in self.session.execute(stmt)}

    def last_received_at(self, round_id: str) -> datetime | None:
        stmt = select(func.max(EventLog.received_at)).where(
            EventLog.round_id == round_id
        )
        return self.session.execute(stmt).scalar()

    def distinct_rounds(self) -> list[str]:
        stmt = (
            select(EventLog.round_id)
            .where(EventLog.round_id.is_not(None))
            .distinct()
            .order_by(EventLog.round_id)
        )
        return [r for r in self.session.execute(stmt).scalars()]

    def total_count(self) -> int:
        return self.session.execute(select(func.count(EventLog.log_id))).scalar() or 0


def _payload_actor_matches(payload: dict | None, actor: str) -> bool:
    """payload 안에서 행위자로 볼 수 있는 필드들을 훑는다."""
    if not payload:
        return False
    for key in ("actor", "approver", "reported_by", "requested_by", "user", "hr_manager"):
        if str(payload.get(key, "")) == actor:
            return True
    return False


class KpiRepository:
    """kpi_snapshots — 시점별 append, 최신값 read"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        round_id: str | None,
        metric_name: str,
        value: float,
        labels: dict | None = None,
        captured_at: datetime | None = None,
    ) -> KpiSnapshot:
        """스냅샷 기록.

        같은 (round_id, metric_name)에 대해 captured_at이 반드시 단조 증가하도록
        보정한다. 최신값 조회가 captured_at DESC 정렬에 의존하기 때문.
        """
        ts = to_naive_utc(captured_at) if captured_at else utcnow()
        prev = self.latest(round_id, metric_name)
        if prev is not None and prev.captured_at >= ts:
            ts = prev.captured_at + timedelta(microseconds=1)

        row = KpiSnapshot(
            snapshot_id=str(uuid4()),
            round_id=round_id,
            metric_name=metric_name,
            value=float(value),
            labels=labels or {},
            captured_at=ts,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def latest(self, round_id: str | None, metric_name: str) -> KpiSnapshot | None:
        stmt = (
            select(KpiSnapshot)
            .where(
                KpiSnapshot.round_id == round_id,
                KpiSnapshot.metric_name == metric_name,
            )
            .order_by(KpiSnapshot.captured_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def latest_value(
        self, round_id: str | None, metric_name: str, default: float | None = None
    ) -> float | None:
        row = self.latest(round_id, metric_name)
        return row.value if row is not None else default

    def increment(
        self,
        round_id: str | None,
        metric_name: str,
        delta: float = 1.0,
        labels: dict | None = None,
    ) -> float:
        current = self.latest_value(round_id, metric_name, 0.0) or 0.0
        new_value = current + delta
        self.record(round_id, metric_name, new_value, labels)
        return new_value

    def history(self, round_id: str | None, metric_name: str) -> list[KpiSnapshot]:
        stmt = (
            select(KpiSnapshot)
            .where(
                KpiSnapshot.round_id == round_id,
                KpiSnapshot.metric_name == metric_name,
            )
            .order_by(KpiSnapshot.captured_at.asc())
        )
        return list(self.session.execute(stmt).scalars())


class OrgStatRepository:
    """org_response_stats — 조직별 응답 집계"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, round_id: str, org: str) -> OrgResponseStat | None:
        return self.session.get(OrgResponseStat, {"round_id": round_id, "org": org})

    def get_or_create(self, round_id: str, org: str) -> OrgResponseStat:
        row = self.get(round_id, org)
        if row is None:
            row = OrgResponseStat(
                round_id=round_id,
                org=org,
                mean_hours=None,
                completion_rate=None,
                predicted_slow=False,
                updated_at=utcnow(),
                invited_count=0,
                responded_count=0,
                total_hours=0.0,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def by_round(self, round_id: str) -> list[OrgResponseStat]:
        stmt = (
            select(OrgResponseStat)
            .where(OrgResponseStat.round_id == round_id)
            .order_by(OrgResponseStat.org)
        )
        return list(self.session.execute(stmt).scalars())


class ReportRepository:
    """reports — 리포트 캐시"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(self, round_id: str, report_type: str) -> Report | None:
        stmt = (
            select(Report)
            .where(Report.round_id == round_id, Report.report_type == report_type)
            .order_by(Report.generated_at.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def save(self, round_id: str, report_type: str, content: dict) -> Report:
        row = Report(
            report_id=str(uuid4()),
            round_id=round_id,
            report_type=report_type,
            content=content,
            generated_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def invalidate(self, round_id: str, report_type: str | None = None) -> int:
        """캐시 삭제 — 리포트는 파생 캐시이므로 삭제 가능 (event_log는 불변)"""
        stmt = select(Report).where(Report.round_id == round_id)
        if report_type:
            stmt = stmt.where(Report.report_type == report_type)
        rows = list(self.session.execute(stmt).scalars())
        for row in rows:
            self.session.delete(row)
        self.session.flush()
        return len(rows)
