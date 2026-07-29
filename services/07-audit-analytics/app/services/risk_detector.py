"""위험 신호 룰 엔진 — 명세 §Design RISK_RULES 4종 구현

    ("담당자_변경",    is_new_responder)
    ("노쇼_예측",      predict_noshow_score > 0.7)
    ("면접관_피로도",  consecutive_high_load(weeks=3))
    ("조직_회신_지연",  mean_hours > 40)

모든 룰은 event_log와 org_response_stats만 읽는다 (다른 서비스 DB 접근 없음).
"""
from __future__ import annotations

from typing import Callable

import structlog
from sqlalchemy.orm import Session

from app.domain.event import UNKNOWN_ORG, pick, pick_opt_number, pick_org
from app.domain.kpi import RiskSignal, risk_level_from
from app.events import EventType
from app.infrastructure.db import EventLog
from app.infrastructure.repository import EventRepository, OrgStatRepository

log = structlog.get_logger(__name__)

# --- 임계값 -----------------------------------------------------------------
NOSHOW_SCORE_THRESHOLD = 0.7
ORG_DELAY_HOURS = 40.0          # 명세: mean_hours > 40
ORG_DELAY_SEVERE_HOURS = 72.0
FATIGUE_LOAD_THRESHOLD = 24     # 회차당 배정 건수
FATIGUE_WEEKS = 3               # 연속 고부하 판정 구간 (회차 = 주 단위 사이클)

# 노쇼 예측 가중치 (PoC 휴리스틱 — 이벤트에서 관측 가능한 신호만 사용)
NOSHOW_WEIGHTS = {
    "prior_noshow": 0.5,
    "deferred": 0.4,
    "escalated": 0.3,
    "reminder_escalation": 0.2,
}


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _applicant_ids(payload: dict | None) -> list[str]:
    ids = pick(
        payload,
        "noshow_applicant_ids",
        "applicant_ids",
        "participant_ids",
        default=None,
    )
    if ids is None:
        single = pick(payload, "applicant_id", "participant_id", "invitee_id")
        return [str(single)] if single else []
    return [str(i) for i in _as_list(ids)]


# --- 룰 1: 담당자 변경 -------------------------------------------------------


def detect_responder_change(events: list[EventLog]) -> list[RiskSignal]:
    """요청받지 않은 사람이 회신한 조직을 찾는다.

    is_new_responder(e): REQUEST_SENT로 초대된 담당자 집합에 없는 응답자,
    또는 payload가 명시적으로 담당자 교체를 표시한 경우.
    """
    invited_by_org: dict[str, set[str]] = {}
    for row in events:
        if row.event_type != EventType.REQUEST_SENT:
            continue
        org = pick_org(row.payload)
        ids = pick(row.payload, "invitee_ids", "recipients", default=None)
        if ids is None:
            single = pick(row.payload, "invitee_id", "recipient")
            ids = [single] if single else []
        invited_by_org.setdefault(org, set()).update(str(i) for i in _as_list(ids))

    changed: dict[str, str] = {}
    for row in events:
        if row.event_type != EventType.RESPONSE_RECEIVED:
            continue
        org = pick_org(row.payload)
        responder = pick(row.payload, "invitee_id", "responder_id", "responder")

        explicit = bool(pick(row.payload, "responder_changed", default=False)) or bool(
            pick(row.payload, "previous_responder", default=None)
        )
        invited = invited_by_org.get(org) or set()
        implicit = bool(responder) and bool(invited) and str(responder) not in invited

        if explicit or implicit:
            changed[org] = str(responder) if responder else ""

    return [
        RiskSignal(
            type="담당자_변경",
            team=org,
            severity="medium",
            detail=f"요청 대상과 다른 담당자 회신 (responder={responder or '미상'})",
        )
        for org, responder in sorted(changed.items())
        if org != UNKNOWN_ORG or responder
    ]


# --- 룰 2: 노쇼 예측 ---------------------------------------------------------


def predict_noshow_scores(events: list[EventLog]) -> dict[str, float]:
    """지원자별 노쇼 위험 점수 (0.0~1.0).

    관측 신호: 과거 노쇼 이력, 연기 이력, 미회신 에스컬레이션, 리마인더 단계 상승.
    payload에 `noshow_score`가 실려 오면 그 값을 우선한다.
    """
    scores: dict[str, float] = {}

    def bump(applicant_id: str, weight: float) -> None:
        scores[applicant_id] = min(scores.get(applicant_id, 0.0) + weight, 1.0)

    for row in events:
        payload = row.payload or {}

        provided = pick_opt_number(payload, "noshow_score", "risk_score")
        if provided is not None:
            for applicant_id in _applicant_ids(payload):
                scores[applicant_id] = max(scores.get(applicant_id, 0.0), provided)
            continue

        if row.event_type == EventType.NOSHOW_REPORTED:
            for applicant_id in _applicant_ids(payload):
                bump(applicant_id, NOSHOW_WEIGHTS["prior_noshow"])
        elif row.event_type == EventType.PARTICIPANT_DEFERRED:
            for applicant_id in _applicant_ids(payload):
                bump(applicant_id, NOSHOW_WEIGHTS["deferred"])
        elif row.event_type == EventType.NON_RESPONDER_ESCALATED:
            for applicant_id in _applicant_ids(payload):
                bump(applicant_id, NOSHOW_WEIGHTS["escalated"])
        elif row.event_type == EventType.REMINDER_SENT:
            level = pick_opt_number(payload, "level") or 0
            if level >= 2:
                for applicant_id in _applicant_ids(payload):
                    bump(applicant_id, NOSHOW_WEIGHTS["reminder_escalation"])

    return scores


def detect_noshow_risk(events: list[EventLog]) -> list[RiskSignal]:
    scores = predict_noshow_scores(events)
    at_risk = [a for a, s in scores.items() if s > NOSHOW_SCORE_THRESHOLD]
    if not at_risk:
        return []

    count = len(at_risk)
    severity = "low" if count <= 3 else "medium" if count <= 10 else "high"
    return [
        RiskSignal(
            type="노쇼_예측",
            count=count,
            severity=severity,
            detail=f"예측 점수 {NOSHOW_SCORE_THRESHOLD} 초과 지원자 {count}명",
        )
    ]


# --- 룰 3: 면접관 피로도 -----------------------------------------------------


def _interviewer_loads(row: EventLog) -> dict[str, float]:
    """SCHEDULE_* 이벤트에서 면접관별 배정 건수를 추출"""
    loads = pick(row.payload, "interviewer_loads", "loads", default=None)
    if isinstance(loads, dict):
        result = {}
        for key, value in loads.items():
            try:
                result[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        return result

    # 대안 포맷: [{"interviewer": "이OO", "load": 26}, ...]
    entries = pick(row.payload, "interviewers", default=None)
    if isinstance(entries, list):
        result = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = pick(entry, "interviewer", "name", "interviewer_id")
            load = pick_opt_number(entry, "load", "assignments", "count")
            if name and load is not None:
                result[str(name)] = load
        return result
    return {}


def detect_interviewer_fatigue(
    session: Session, round_id: str, weeks: int = FATIGUE_WEEKS
) -> list[RiskSignal]:
    """최근 `weeks`개 회차에서 연속으로 고부하인 면접관을 찾는다.

    회차가 주 단위 사이클이므로 "최근 N회차 연속"을 "N주 연속"으로 본다.
    """
    repo = EventRepository(session)
    rounds = repo.distinct_rounds()
    if round_id in rounds:
        # 대상 회차까지의 최근 N개 회차 (회차 ID 사전순 = 시간순 전제)
        window = rounds[: rounds.index(round_id) + 1][-weeks:]
    else:
        window = rounds[-weeks:]
    if not window:
        return []

    # 회차별 면접관 부하
    load_by_round: dict[str, dict[str, float]] = {}
    for rid in window:
        merged: dict[str, float] = {}
        for row in repo.by_round(
            rid, [EventType.SCHEDULE_GENERATED, EventType.SCHEDULE_LOCKED]
        ):
            for name, load in _interviewer_loads(row).items():
                merged[name] = max(merged.get(name, 0.0), load)
        load_by_round[rid] = merged

    signals: list[RiskSignal] = []
    interviewers = {name for loads in load_by_round.values() for name in loads}
    for name in sorted(interviewers):
        streak = 0
        for rid in window:
            if load_by_round[rid].get(name, 0.0) >= FATIGUE_LOAD_THRESHOLD:
                streak += 1
            else:
                streak = 0
        if streak >= weeks:
            severity = "high"
        elif streak >= 2:
            severity = "medium"
        else:
            continue
        signals.append(
            RiskSignal(
                type="면접관_피로도",
                interviewer=name,
                severity=severity,
                detail=f"{streak}회차 연속 배정 {FATIGUE_LOAD_THRESHOLD}건 이상",
            )
        )
    return signals


# --- 룰 4: 조직 회신 지연 ----------------------------------------------------


def detect_org_delay(session: Session, round_id: str) -> list[RiskSignal]:
    stats = OrgStatRepository(session).by_round(round_id)
    signals: list[RiskSignal] = []
    for stat in stats:
        if stat.mean_hours is None or stat.mean_hours <= ORG_DELAY_HOURS:
            continue
        severity = "high" if stat.mean_hours > ORG_DELAY_SEVERE_HOURS else "medium"
        signals.append(
            RiskSignal(
                type="조직_회신_지연",
                team=stat.org,
                severity=severity,
                detail=f"평균 회신 {stat.mean_hours:.1f}h (임계 {ORG_DELAY_HOURS:.0f}h)",
            )
        )
    return signals


# --- 통합 진입점 -------------------------------------------------------------

RISK_RULES: list[tuple[str, Callable]] = [
    ("담당자_변경", detect_responder_change),
    ("노쇼_예측", detect_noshow_risk),
    ("면접관_피로도", detect_interviewer_fatigue),
    ("조직_회신_지연", detect_org_delay),
]


def detect_risks(session: Session, round_id: str) -> list[RiskSignal]:
    """회차의 위험 신호 전체 감지 (4종 룰)"""
    events = EventRepository(session).by_round(round_id)

    signals: list[RiskSignal] = []
    signals.extend(detect_responder_change(events))
    signals.extend(detect_noshow_risk(events))
    signals.extend(detect_interviewer_fatigue(session, round_id))
    signals.extend(detect_org_delay(session, round_id))

    # 심각도 높은 순으로 정렬 (대시보드 상단 노출용)
    order = {"high": 0, "medium": 1, "low": 2}
    signals.sort(key=lambda s: (order.get(s.severity, 3), s.type))
    return signals


def overall_risk_level(signals: list[RiskSignal]) -> str:
    """회차 종합 위험도 — /dashboard/kpi 의 `위험도`"""
    return risk_level_from([s.severity for s in signals])
