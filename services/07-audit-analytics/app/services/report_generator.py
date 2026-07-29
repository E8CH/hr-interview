"""리포트 생성기 — 회차 종합 리포트 · Before/After 비교

명세 §Design "리포트 생성 파이프라인":
    1. 요청 수신
    2. event_log에서 해당 회차 이벤트 전체 로드
    3. 단계별 지속 시간 계산
    4. Service 04의 규칙 준수율 조회 (API 호출 — PoC는 mock)
    5. JSON 리포트 생성 → reports 캐시 저장
    6. 재요청 시 캐시 즉시 반환 (동일 회차 새 이벤트 도착 시 무효화)
"""
from __future__ import annotations

from datetime import datetime

import structlog
from sqlalchemy.orm import Session

from app.domain.kpi import BEFORE_AFTER_METRICS, Metric
from app.domain.report import PHASE_DEFINITIONS, ReportType
from app.infrastructure.repository import (
    EventRepository,
    KpiRepository,
    OrgStatRepository,
    ReportRepository,
)
from app.services.clients.scheduler_client import SchedulerClient
from app.services.risk_detector import detect_risks, overall_risk_level

log = structlog.get_logger(__name__)


def round_metric(value: float | None) -> float | None:
    """표시용 반올림.

    1 미만 값은 소수 3자리까지 살린다. 0.05h(=3분) 같은 값이
    소수 1자리 반올림에서 0.1로 뭉개지는 것을 막기 위함.
    """
    if value is None:
        return None
    return round(value, 3) if abs(value) < 1 else round(value, 1)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class ReportGenerator:
    def __init__(
        self, session: Session, scheduler_client: SchedulerClient | None = None
    ) -> None:
        self.session = session
        self.events = EventRepository(session)
        self.kpi = KpiRepository(session)
        self.orgs = OrgStatRepository(session)
        self.reports = ReportRepository(session)
        self.scheduler = scheduler_client or SchedulerClient()

    # --- 캐시 --------------------------------------------------------------

    def cached_round_summary(self, round_id: str) -> dict | None:
        """유효한 캐시가 있으면 반환. 새 이벤트가 도착했으면 None."""
        cached = self.reports.latest(round_id, ReportType.ROUND_SUMMARY)
        if cached is None or not cached.content:
            return None
        last_event = self.events.last_received_at(round_id)
        if last_event is not None and cached.generated_at < last_event:
            return None  # 캐시 무효 — 새 이벤트 도착
        return cached.content

    # --- 회차 종합 리포트 ---------------------------------------------------

    def round_summary(self, round_id: str, use_cache: bool = True) -> tuple[dict, bool]:
        """(리포트, 캐시 적중 여부)"""
        if use_cache:
            cached = self.cached_round_summary(round_id)
            if cached is not None:
                return cached, True

        events = self.events.by_round(round_id)
        report = {
            "round_id": round_id,
            "duration_hours": round_metric(self._total_duration_hours(events)) or 0.0,
            "phases": self._phases(events),
            "rule_compliance": self._rule_compliance(round_id),
            "noshow_count": int(self.kpi.latest_value(round_id, Metric.NOSHOW_COUNT, 0) or 0),
            "repair_events": int(self.kpi.latest_value(round_id, Metric.REPAIR_COUNT, 0) or 0),
            "event_counts": self.events.count_by_type(round_id),
            "total_events": len(events),
            "kpi": self.kpi_summary(round_id),
            "organizations": self.org_summary(round_id),
            "risks": [s.to_response() for s in detect_risks(self.session, round_id)],
            "generated_at": _iso(datetime.utcnow()),
        }

        self.reports.save(round_id, ReportType.ROUND_SUMMARY, report)
        self.session.commit()
        return report, False

    def _total_duration_hours(self, events: list) -> float:
        if len(events) < 2:
            return 0.0
        return (events[-1].timestamp - events[0].timestamp).total_seconds() / 3600.0

    def _phases(self, events: list) -> list[dict]:
        """단계별 시작·종료·소요시간.

        이벤트가 없는 단계는 duration 0으로 남긴다 (단계 누락을 숨기지 않기 위해).
        """
        by_type: dict[str, list[datetime]] = {}
        for row in events:
            by_type.setdefault(row.event_type, []).append(row.timestamp)

        def resolve(groups: list[list[str]], mode: str) -> datetime | None:
            for group in groups:
                stamps = [ts for t in group for ts in by_type.get(t, [])]
                if stamps:
                    return min(stamps) if mode == "first" else max(stamps)
            return None

        phases = []
        for name, start_groups, end_groups, end_mode in PHASE_DEFINITIONS:
            start = resolve(start_groups, "first")
            end = resolve(end_groups, end_mode)
            duration = 0.0
            if start is not None and end is not None and end > start:
                duration = (end - start).total_seconds() / 3600.0
            phases.append(
                {
                    "phase": name,
                    "start": _iso(start),
                    "end": _iso(end),
                    "duration_h": round_metric(duration) or 0.0,
                }
            )
        return phases

    def _rule_compliance(self, round_id: str) -> dict:
        """Service 04 규칙별 준수율 + 이벤트에서 관측된 전체 준수율"""
        rules = self.scheduler.get_rule_compliance(round_id)
        overall = self.kpi.latest_value(round_id, Metric.RULE_COMPLIANCE)
        if overall is None and rules:
            overall = sum(rules.values()) / len(rules)
        return {
            "overall": round_metric(overall) or 0.0,
            "rules": {k: round_metric(v) for k, v in rules.items()},
            "violations": int(
                self.kpi.latest_value(round_id, Metric.RULE_VIOLATION_COUNT, 0) or 0
            ),
        }

    def kpi_summary(self, round_id: str) -> dict:
        """리포트에 포함할 KPI 스냅샷 (대시보드와 동일 소스)"""
        risks = detect_risks(self.session, round_id)
        get = self.kpi.latest_value
        return {
            "총_대상자": int(get(round_id, Metric.TOTAL_TARGETS, 0) or 0),
            "회신_완료": int(get(round_id, Metric.RESPONSE_DONE, 0) or 0),
            "회신_대기": int(get(round_id, Metric.RESPONSE_PENDING, 0) or 0),
            "배치_완료율": round_metric(get(round_id, Metric.ASSIGN_RATE, 0.0)) or 0.0,
            "규칙_준수율": round_metric(get(round_id, Metric.RULE_COMPLIANCE, 0.0)) or 0.0,
            "위험도": overall_risk_level(risks),
            "실행_시간_초": round_metric(get(round_id, Metric.EXEC_SECONDS, 0.0)) or 0.0,
        }

    def org_summary(self, round_id: str) -> list[dict]:
        from app.domain.kpi import temperature_of

        return [
            {
                "org": stat.org,
                "avg_response_h": round_metric(stat.mean_hours) or 0.0,
                "completion": round_metric(stat.completion_rate) or 0.0,
                "temperature": temperature_of(stat.mean_hours),
                "predicted_slow": bool(stat.predicted_slow),
            }
            for stat in self.orgs.by_round(round_id)
        ]

    # --- Before/After 비교 --------------------------------------------------

    def _metric_value(self, round_id: str, metric: str) -> float | None:
        """비교 지표 조회.

        도입 이전 회차(baseline)는 이벤트가 없으므로 kpi_snapshots에 직접
        주입된 값을 읽는다. 도입 이후 회차는 프로젝터가 같은 메트릭을
        이벤트에서 계산해 넣으므로 읽는 경로가 동일하다.
        """
        return self.kpi.latest_value(round_id, metric)

    def before_after(self, before_round: str, after_round: str) -> dict:
        result: dict[str, dict] = {}
        for metric, delta_mode in BEFORE_AFTER_METRICS:
            before = self._metric_value(before_round, metric)
            after = self._metric_value(after_round, metric)

            entry: dict = {
                "before": round_metric(before),
                "after": round_metric(after),
            }
            if before is not None and after is not None:
                if delta_mode == "pct":
                    entry["delta_pct"] = (
                        round((after - before) / before * 100.0, 1)
                        if before != 0
                        else None
                    )
                else:
                    entry["delta_pp"] = round(after - before, 1)
            result[metric] = {k: v for k, v in entry.items() if v is not None}
        return result
