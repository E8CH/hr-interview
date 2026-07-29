"""프로젝터 — 이벤트를 KPI로 투영 (event_log → kpi_snapshots / org_response_stats)

명세 §Design "프로젝션 규칙"을 18종 이벤트 전체로 확장한 구현.
프로젝션은 순수 파생 데이터이며 event_log는 절대 변경하지 않는다.
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.domain.event import (
    UNKNOWN_ORG,
    parse_dt,
    pick,
    pick_int,
    pick_number,
    pick_opt_number,
    pick_org,
)
from app.domain.kpi import Metric, severity_weight
from app.events import EventType
from app.infrastructure.db import EventLog, utcnow
from app.infrastructure.repository import (
    EventRepository,
    KpiRepository,
    OrgStatRepository,
)

log = structlog.get_logger(__name__)

# 조직 회신이 느리다고 볼 임계 (명세 위험 룰: mean_hours > 40)
SLOW_ORG_HOURS = 40.0
SLOW_ORG_COMPLETION = 70.0

# 위험 신호를 유발하는 이벤트 → 스냅샷에 남길 severity
RISK_TRIGGER_SEVERITY: dict[str, str] = {
    EventType.INTEGRITY_VIOLATED: "high",
    EventType.RULE_VIOLATED: "medium",
    EventType.NOSHOW_REPORTED: "medium",
    EventType.REPAIR_EXECUTED: "low",
    EventType.NON_RESPONDER_ESCALATED: "medium",
    EventType.NOTIFICATION_FAILED: "medium",
    EventType.PARTICIPANT_DEFERRED: "low",
}


def normalize_pct(value: float | None) -> float | None:
    """0~1 스케일과 0~100 스케일을 모두 받아 0~100으로 정규화.

    계약(`rule_compliance_overall: float`)이 스케일을 규정하지 않아
    1.0 이하이면 비율로 간주한다.
    """
    if value is None:
        return None
    return value * 100.0 if 0.0 <= value <= 1.0 else value


class Projector:
    """단일 이벤트를 받아 파생 지표를 갱신한다."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.events = EventRepository(session)
        self.kpi = KpiRepository(session)
        self.orgs = OrgStatRepository(session)

    # --- 진입점 ---------------------------------------------------------

    def project(self, row: EventLog) -> None:
        if not row.round_id:
            # round_id 없는 이벤트는 event_log에만 남기고 투영하지 않는다
            return
        handler = _HANDLERS.get(row.event_type)
        if handler is None:
            log.debug("projector.unhandled_event", event_type=row.event_type)
            return
        handler(self, row)
        self._record_risk_trigger(row)

    # --- 공통 헬퍼 -------------------------------------------------------

    def _set(self, row: EventLog, metric: str, value: float, labels: dict | None = None) -> None:
        self.kpi.record(row.round_id, metric, value, labels, captured_at=row.timestamp)

    def _inc(self, row: EventLog, metric: str, delta: float = 1.0) -> float:
        current = self.kpi.latest_value(row.round_id, metric, 0.0) or 0.0
        new_value = current + delta
        self._set(row, metric, new_value)
        return new_value

    def _get(self, round_id: str, metric: str, default: float = 0.0) -> float:
        value = self.kpi.latest_value(round_id, metric, default)
        return default if value is None else value

    def _record_risk_trigger(self, row: EventLog) -> None:
        severity = RISK_TRIGGER_SEVERITY.get(row.event_type)
        if severity is None:
            return
        self._set(
            row,
            Metric.RISK_LEVEL,
            float(severity_weight(severity)),
            {"trigger": row.event_type, "severity": severity},
        )

    def _recompute_pending(self, row: EventLog) -> None:
        invited = self._get(row.round_id, Metric.INVITED_TOTAL)
        done = self._get(row.round_id, Metric.RESPONSE_DONE)
        if invited <= 0:
            # 요청 발송 이벤트가 아직 없으면 총 대상자로 갈음
            invited = self._get(row.round_id, Metric.TOTAL_TARGETS)
        self._set(row, Metric.RESPONSE_PENDING, max(invited - done, 0.0))

    def _recompute_response_aggregates(self, row: EventLog) -> None:
        """조직 집계로부터 전사 회신 지표를 재계산"""
        stats = self.orgs.by_round(row.round_id)
        responded = sum(s.responded_count for s in stats)
        invited = sum(s.invited_count for s in stats)
        total_hours = sum(s.total_hours for s in stats)

        if responded > 0:
            self._set(row, Metric.RESPONSE_LEADTIME_H, total_hours / responded)
        if invited > 0:
            self._set(row, Metric.RESPONSE_COMPLETION, responded / invited * 100.0)

    def _update_org_stat(self, round_id: str, org: str) -> None:
        stat = self.orgs.get_or_create(round_id, org)
        if stat.responded_count > 0:
            stat.mean_hours = stat.total_hours / stat.responded_count
        if stat.invited_count > 0:
            stat.completion_rate = stat.responded_count / stat.invited_count * 100.0
        stat.predicted_slow = bool(
            (stat.mean_hours is not None and stat.mean_hours > SLOW_ORG_HOURS)
            or (
                stat.completion_rate is not None
                and stat.completion_rate < SLOW_ORG_COMPLETION
            )
        )
        stat.updated_at = utcnow()
        self.session.flush()

    # --- Service 01: Version Manager ------------------------------------

    def on_master_registered(self, row: EventLog) -> None:
        count = pick_int(row.payload, "applicant_count", "total_applicants")
        if count:
            self._set(row, Metric.TOTAL_TARGETS, count)
        self._recompute_pending(row)

    def on_distribution_registered(self, row: EventLog) -> None:
        self._inc(row, Metric.TEAM_DISTRIBUTION_COUNT)
        org = pick_org(row.payload)
        if org != UNKNOWN_ORG:
            stat = self.orgs.get_or_create(row.round_id, org)
            invited = pick_int(row.payload, "applicant_count")
            if invited and not stat.invited_count:
                # 배포 등록 시점의 팀 인원 — REQUEST_SENT가 오면 덮어쓴다
                stat.invited_count = invited
                self._update_org_stat(row.round_id, org)

    def on_integrity_violated(self, row: EventLog) -> None:
        issues = pick(row.payload, "issues", default=[]) or []
        self._inc(row, Metric.INTEGRITY_VIOLATION_COUNT, max(len(issues), 1))

    # --- Service 02: Distributor ----------------------------------------

    def on_distribution_plan_created(self, row: EventLog) -> None:
        self._inc(row, Metric.PLAN_CREATED_COUNT)
        total = pick_int(row.payload, "total_applicants")
        if total and self._get(row.round_id, Metric.TOTAL_TARGETS) <= 0:
            self._set(row, Metric.TOTAL_TARGETS, total)

        team_counts = pick(row.payload, "team_counts", default={}) or {}
        if isinstance(team_counts, dict):
            for org, count in team_counts.items():
                stat = self.orgs.get_or_create(row.round_id, str(org))
                if not stat.invited_count:
                    stat.invited_count = int(count or 0)
                    self._update_org_stat(row.round_id, str(org))

    def on_distribution_approved(self, row: EventLog) -> None:
        total = pick_int(row.payload, "total_applicants")
        if total:
            self._set(row, Metric.TOTAL_TARGETS, total)
        self._recompute_pending(row)

    def on_distribution_adjusted(self, row: EventLog) -> None:
        self._inc(row, Metric.DISTRIBUTION_ADJUST_COUNT)

    # --- Service 03: Response Collector ---------------------------------

    def on_request_sent(self, row: EventLog) -> None:
        invitee_count = pick_int(row.payload, "invitee_count", "recipient_count")

        # 조직별 초대 인원: org+invitee_count 또는 org_invitee_counts dict 지원
        org_counts = pick(row.payload, "org_invitee_counts", "team_counts", default=None)
        if isinstance(org_counts, dict) and org_counts:
            for org, count in org_counts.items():
                stat = self.orgs.get_or_create(row.round_id, str(org))
                stat.invited_count = int(count or 0)
                self._update_org_stat(row.round_id, str(org))
            if not invitee_count:
                invitee_count = sum(int(v or 0) for v in org_counts.values())
        else:
            org = pick_org(row.payload)
            if org != UNKNOWN_ORG and invitee_count:
                stat = self.orgs.get_or_create(row.round_id, org)
                stat.invited_count = invitee_count
                self._update_org_stat(row.round_id, org)

        if invitee_count:
            self._inc(row, Metric.INVITED_TOTAL, invitee_count)
        self._recompute_pending(row)

    def _response_hours(self, row: EventLog) -> float | None:
        """회신 소요시간(h) 산출 — payload 우선, 없으면 REQUEST_SENT 기준"""
        direct = pick(row.payload, "response_hours", "elapsed_hours", "duration_h")
        if direct is not None:
            try:
                return max(float(direct), 0.0)
            except (TypeError, ValueError):
                pass

        submitted = parse_dt(pick(row.payload, "submitted_at")) or row.timestamp
        requested = parse_dt(pick(row.payload, "requested_at", "sent_at"))
        if requested is None:
            requested = self.events.first_timestamp(
                row.round_id, [EventType.REQUEST_SENT]
            )
        if requested is None or submitted is None:
            return None
        return max((submitted - requested).total_seconds() / 3600.0, 0.0)

    def on_response_received(self, row: EventLog) -> None:
        self._inc(row, Metric.RESPONSE_DONE)

        org = pick_org(row.payload)
        hours = self._response_hours(row)
        stat = self.orgs.get_or_create(row.round_id, org)
        stat.responded_count += 1
        if hours is not None:
            stat.total_hours += hours
        self._update_org_stat(row.round_id, org)

        self._recompute_pending(row)
        self._recompute_response_aggregates(row)

    def on_reminder_sent(self, row: EventLog) -> None:
        self._inc(row, Metric.REMINDER_COUNT)

    def on_non_responder_escalated(self, row: EventLog) -> None:
        self._inc(row, Metric.ESCALATION_COUNT)

    # --- Service 04: Scheduler ------------------------------------------

    def on_schedule_generated(self, row: EventLog) -> None:
        assigned = pick_int(row.payload, "total_assigned", "assignments_count")
        if assigned:
            self._set(row, Metric.ASSIGNED_TOTAL, assigned)

        coverage = normalize_pct(
            pick_opt_number(row.payload, "coverage_pct", "coverage")
        )
        if coverage is None and assigned:
            total = self._get(row.round_id, Metric.TOTAL_TARGETS)
            coverage = assigned / total * 100.0 if total > 0 else None
        if coverage is not None:
            self._set(row, Metric.ASSIGN_RATE, coverage)

        compliance = normalize_pct(
            pick_opt_number(
                row.payload,
                "rule_compliance_overall",
                "rule_compliance",
                "compliance_pct",
            )
        )
        if compliance is not None:
            self._set(row, Metric.RULE_COMPLIANCE, compliance)

    def on_schedule_locked(self, row: EventLog) -> None:
        assigned = pick_int(row.payload, "assignments_count", "total_assigned")
        if assigned:
            self._set(row, Metric.ASSIGNED_TOTAL, assigned)
            total = self._get(row.round_id, Metric.TOTAL_TARGETS)
            if total > 0:
                self._set(row, Metric.ASSIGN_RATE, assigned / total * 100.0)

        # 배치 단계 소요: SCHEDULE_GENERATED → SCHEDULE_LOCKED
        started = self.events.first_timestamp(
            row.round_id, [EventType.SCHEDULE_GENERATED]
        )
        phase_seconds = (
            (row.timestamp - started).total_seconds() if started is not None else None
        )
        if phase_seconds is not None and phase_seconds >= 0:
            self._set(row, Metric.ASSIGN_DURATION_H, phase_seconds / 3600.0)

        # 실행 시간: 스케줄러가 보고한 알고리즘 실행 시간 우선,
        # 없으면 배치 단계 소요 시간으로 갈음
        exec_seconds = pick_number(
            row.payload, "execution_seconds", "exec_seconds", "elapsed_seconds",
            default=-1.0,
        )
        if exec_seconds < 0:
            exec_seconds = phase_seconds if phase_seconds is not None else 0.0
        self._set(row, Metric.EXEC_SECONDS, max(exec_seconds, 0.0))

        compliance = normalize_pct(
            pick_opt_number(row.payload, "rule_compliance_overall", "compliance_pct")
        )
        if compliance is not None:
            self._set(row, Metric.RULE_COMPLIANCE, compliance)

    def on_rule_violated(self, row: EventLog) -> None:
        violations = pick(row.payload, "violations", default=None)
        count = len(violations) if isinstance(violations, list) and violations else 1
        self._inc(row, Metric.RULE_VIOLATION_COUNT, count)

        compliance = normalize_pct(
            pick_opt_number(row.payload, "compliance_pct", "rule_compliance_overall")
        )
        if compliance is not None:
            self._set(row, Metric.RULE_COMPLIANCE, compliance)

    # --- Service 05: Repair Engine --------------------------------------

    def on_noshow_reported(self, row: EventLog) -> None:
        ids = pick(row.payload, "noshow_applicant_ids", "applicant_ids", default=None)
        count = len(ids) if isinstance(ids, list) and ids else pick_int(
            row.payload, "count", default=1
        )
        self._inc(row, Metric.NOSHOW_COUNT, max(count, 1))

    def on_repair_executed(self, row: EventLog) -> None:
        repairs = self._inc(row, Metric.REPAIR_COUNT)

        # 노쇼 대응 시간: 직전 NOSHOW_REPORTED → 이 REPAIR_EXECUTED
        origin = self.events.last_before(
            row.round_id, [EventType.NOSHOW_REPORTED], row.timestamp
        )
        hours = pick_number(row.payload, "response_hours", default=-1.0)
        if hours < 0 and origin is not None:
            hours = max((row.timestamp - origin.timestamp).total_seconds() / 3600.0, 0.0)
        if hours >= 0:
            total = self._get(row.round_id, Metric.NOSHOW_RESPONSE_TOTAL_H) + hours
            self._set(row, Metric.NOSHOW_RESPONSE_TOTAL_H, total)
            self._set(row, Metric.NOSHOW_RESPONSE_H, total / repairs)

        deferred = pick_int(row.payload, "deferred")
        if deferred:
            self._inc(row, Metric.DEFERRED_COUNT, deferred)

    def on_participant_deferred(self, row: EventLog) -> None:
        self._inc(row, Metric.DEFERRED_COUNT)

    # --- Service 06: Notification Hub -----------------------------------

    def on_notification_sent(self, row: EventLog) -> None:
        self._inc(row, Metric.NOTIFY_SENT_COUNT)

    def on_notification_failed(self, row: EventLog) -> None:
        self._inc(row, Metric.NOTIFY_FAILED_COUNT)


# 18종 전체 디스패치 테이블
_HANDLERS = {
    EventType.MASTER_REGISTERED: Projector.on_master_registered,
    EventType.DISTRIBUTION_REGISTERED: Projector.on_distribution_registered,
    EventType.INTEGRITY_VIOLATED: Projector.on_integrity_violated,
    EventType.DISTRIBUTION_PLAN_CREATED: Projector.on_distribution_plan_created,
    EventType.DISTRIBUTION_APPROVED: Projector.on_distribution_approved,
    EventType.DISTRIBUTION_ADJUSTED: Projector.on_distribution_adjusted,
    EventType.REQUEST_SENT: Projector.on_request_sent,
    EventType.RESPONSE_RECEIVED: Projector.on_response_received,
    EventType.REMINDER_SENT: Projector.on_reminder_sent,
    EventType.NON_RESPONDER_ESCALATED: Projector.on_non_responder_escalated,
    EventType.SCHEDULE_GENERATED: Projector.on_schedule_generated,
    EventType.SCHEDULE_LOCKED: Projector.on_schedule_locked,
    EventType.RULE_VIOLATED: Projector.on_rule_violated,
    EventType.NOSHOW_REPORTED: Projector.on_noshow_reported,
    EventType.REPAIR_EXECUTED: Projector.on_repair_executed,
    EventType.PARTICIPANT_DEFERRED: Projector.on_participant_deferred,
    EventType.NOTIFICATION_SENT: Projector.on_notification_sent,
    EventType.NOTIFICATION_FAILED: Projector.on_notification_failed,
}
