"""PoC 데모 데이터 — 18종 이벤트 스트림 · Before 회차 baseline 스냅샷

**운영 코드가 아니다.** 시드 스크립트(`scripts/seed_poc.py`)와 테스트가
같은 데이터를 쓰도록 한 곳에 모아둔 것이다.

--- 설계 노트 ---------------------------------------------------------------
1. Before 회차(R2025-Q4-04)는 시스템 도입 *이전*이라 이벤트가 존재하지 않는다.
   따라서 kpi_snapshots에 직접 주입한다. After 회차는 동일한 메트릭을
   프로젝터가 이벤트에서 계산하므로, 비교 리포트는 양쪽을 같은 경로로 읽는다.

2. 회차 전체 소요 3.2h(압축된 처리 시계)와 평균 회신 소요 11.8h는 측정 기준이
   다르다. 전자는 시스템이 회차를 처리한 벽시계 시간, 후자는 면접위원 관점의
   요청→회신 경과 시간(Service 03이 payload의 `response_hours`로 보고)이다.
   명세의 예시 수치가 두 기준을 함께 제시하므로 그대로 재현한다.

3. 조직/인원 구성은 명세 예시(제1기술원 6h·95%, 제3기술원 52h·62%)를 만족하면서
   전사 회신 완료율 92%, 평균 회신 11.8h가 정확히 떨어지도록 역산했다.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.domain.kpi import Metric
from app.events import EventType
from app.infrastructure.repository import KpiRepository

BASELINE_ROUND = "R2025-Q4-04"          # 도입 전 (이벤트 없음)
DEMO_ROUND = "R2026-Q3-01"              # 도입 후 (이벤트 스트림)
HISTORY_ROUNDS = ["R2026-Q1-01", "R2026-Q2-01"]  # 면접관 피로도 판정용 과거 회차

DEMO_BASE_TIME = datetime(2026, 7, 29, 9, 0, 0)

# 도입 전 실측 지표 (PPT 검증 수치)
BASELINE_METRICS: dict[str, float] = {
    Metric.RESPONSE_LEADTIME_H: 29.9,
    Metric.RESPONSE_COMPLETION: 86.0,
    Metric.ASSIGN_DURATION_H: 32.0,
    Metric.RULE_COMPLIANCE: 65.0,
    Metric.NOSHOW_RESPONSE_H: 8.0,
}

# 조직별 구성: (조직, 지원자 수, 면접위원 초대 수, 회신 수, 회신당 소요 h)
#   전사 회신 완료율 = 46/50 = 92.0%
#   전사 평균 회신 = (19*6 + 12*9 + 5*52 + 10*6.08) / 46 = 11.8h
ORG_PROFILE: list[tuple[str, int, int, int, float]] = [
    ("제1기술원", 30, 20, 19, 6.00),
    ("제2사업부", 22, 12, 12, 9.00),
    ("제3기술원", 16, 8, 5, 52.00),
    ("제4연구소", 20, 10, 10, 6.08),
]

TOTAL_APPLICANTS = sum(p[1] for p in ORG_PROFILE)   # 88
TOTAL_INVITED = sum(p[2] for p in ORG_PROFILE)      # 50
TOTAL_RESPONSES = sum(p[3] for p in ORG_PROFILE)    # 46

# 노쇼 발생 지원자 (NOSHOW_REPORTED 0.5 + PARTICIPANT_DEFERRED 0.4 = 0.9 > 0.7)
NOSHOW_APPLICANTS = ["A-0012", "A-0034", "A-0056"]

# 면접관별 배정 부하 — 이OO는 3회차 연속 24건 이상 → 피로도 high
INTERVIEWER_LOADS = {"이OO": 26, "김OO": 18, "박OO": 22}
HISTORY_LOADS = {"이OO": 25, "김OO": 12, "박OO": 20}

# 제3기술원에서 원래 요청받지 않은 담당자가 회신 → 담당자_변경 감지
SUBSTITUTE_RESPONDER = "IV-SUB-01"


def _invitee_ids(org_index: int, count: int) -> list[str]:
    return [f"IV{org_index}{i:02d}" for i in range(1, count + 1)]


class _EventBuilder:
    def __init__(self, round_id: str, base_time: datetime) -> None:
        self.round_id = round_id
        self.base_time = base_time
        self.correlation_id = str(uuid4())
        self.events: list[dict] = []

    def add(
        self,
        event_type: str,
        minutes: float,
        payload: dict,
        producer: str = "unknown",
    ) -> dict:
        envelope = {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "timestamp": (self.base_time + timedelta(minutes=minutes)).isoformat(),
            "round_id": self.round_id,
            "producer": producer,
            "correlation_id": self.correlation_id,
            "payload": payload,
        }
        self.events.append(envelope)
        return envelope


def build_demo_events(
    round_id: str = DEMO_ROUND, base_time: datetime | None = None
) -> list[dict]:
    """회차 1건의 18종 이벤트 스트림 (시간 오름차순).

    타임라인 (T0 = 09:00, 총 3.2h)
        T+0    MASTER_REGISTERED          자료취합 시작
        T+5    INTEGRITY_VIOLATED
        T+6    DISTRIBUTION_REGISTERED×4  자료취합 종료 (0.1h)
        T+6    DISTRIBUTION_PLAN_CREATED  배포 시작
        T+20   DISTRIBUTION_ADJUSTED
        T+24   DISTRIBUTION_APPROVED      배포 종료 (0.3h)
        T+25   REQUEST_SENT×4             회신수집 시작
        T+30~133 RESPONSE_RECEIVED×46     회신수집 종료 (1.8h)
        T+140  SCHEDULE_GENERATED         배치 시작
        T+143  SCHEDULE_LOCKED            배치 종료 (0.05h)
        T+143~149 NOTIFICATION_*          안내 (0.1h)
        T+180  NOSHOW_REPORTED
        T+186  REPAIR_EXECUTED            노쇼 대응 0.1h
        T+192  PARTICIPANT_DEFERRED       회차 종료 (3.2h)
    """
    builder = _EventBuilder(round_id, base_time or DEMO_BASE_TIME)

    # --- 자료취합 ---
    builder.add(
        EventType.MASTER_REGISTERED,
        0,
        {
            "version_id": f"{round_id}-v1",
            "fingerprint": "sha256:" + "0" * 8,
            "applicant_count": TOTAL_APPLICANTS,
            "actor": "HR김민지",
        },
        producer="version-manager",
    )
    builder.add(
        EventType.INTEGRITY_VIOLATED,
        5,
        {
            "status": "WARN",
            "duplicate_count": 1,
            "undistributed_count": 0,
            "issues": [{"type": "DUPLICATE", "applicant_id": "A-0007"}],
        },
        producer="version-manager",
    )
    for org, applicants, _, _, _ in ORG_PROFILE:
        builder.add(
            EventType.DISTRIBUTION_REGISTERED,
            6,
            {
                "version_id": f"{round_id}-v1",
                "team_name": org,
                "org": org,
                "fingerprint": "sha256:" + "1" * 8,
                "applicant_count": applicants,
                "actor": "HR김민지",
            },
            producer="version-manager",
        )

    # --- 배포 ---
    builder.add(
        EventType.DISTRIBUTION_PLAN_CREATED,
        6,
        {
            "plan_id": f"{round_id}-plan",
            "team_counts": {org: applicants for org, applicants, _, _, _ in ORG_PROFILE},
            "total_applicants": TOTAL_APPLICANTS,
        },
        producer="distributor",
    )
    builder.add(
        EventType.DISTRIBUTION_ADJUSTED,
        20,
        {"plan_id": f"{round_id}-plan", "moved": 2, "actor": "HR김민지"},
        producer="distributor",
    )
    builder.add(
        EventType.DISTRIBUTION_APPROVED,
        24,
        {
            "plan_id": f"{round_id}-plan",
            "approver": "HR김민지",
            "total_applicants": TOTAL_APPLICANTS,
        },
        producer="distributor",
    )

    # --- 회신수집 ---
    deadline = (builder.base_time + timedelta(days=2)).isoformat()
    responses: list[tuple[str, str, float]] = []  # (org, invitee_id, hours)
    for index, (org, _, invited, responded, hours) in enumerate(ORG_PROFILE, start=1):
        ids = _invitee_ids(index, invited)
        builder.add(
            EventType.REQUEST_SENT,
            25,
            {
                "request_id": f"{round_id}-req-{index}",
                "org": org,
                "invitee_count": invited,
                "invitee_ids": ids,
                "deadline": deadline,
            },
            producer="response-collector",
        )
        responder_ids = ids[:responded]
        if org == "제3기술원":
            # 담당자 교체: 마지막 회신자는 요청 대상이 아니었던 사람
            responder_ids = responder_ids[:-1] + [SUBSTITUTE_RESPONDER]
        responses.extend((org, rid, hours) for rid in responder_ids)

    # 회신을 T+30 ~ T+133 구간에 균등 배치 (회신수집 단계 = 1.8h)
    span = 103.0
    for position, (org, invitee_id, hours) in enumerate(responses):
        minutes = 30.0 + span * position / max(len(responses) - 1, 1)
        builder.add(
            EventType.RESPONSE_RECEIVED,
            minutes,
            {
                "response_id": f"{round_id}-resp-{position:03d}",
                "invitee_id": invitee_id,
                "org": org,
                "submitted_at": (
                    builder.base_time + timedelta(minutes=minutes)
                ).isoformat(),
                # 면접위원 관점의 실제 경과 시간 (Service 03 보고값)
                "response_hours": hours,
            },
            producer="response-collector",
        )

    for offset, level in ((60, 1), (80, 2), (95, 2)):
        builder.add(
            EventType.REMINDER_SENT,
            offset,
            {"invitee_id": "IV305", "level": level, "channel": "email"},
            producer="response-collector",
        )
    for offset, invitee in ((100, "IV306"), (105, "IV307")):
        builder.add(
            EventType.NON_RESPONDER_ESCALATED,
            offset,
            {"invitee_id": invitee, "escalated_to": "팀장", "level": 3},
            producer="response-collector",
        )

    # --- 배치 ---
    assigned = 78
    builder.add(
        EventType.SCHEDULE_GENERATED,
        140,
        {
            "schedule_id": f"{round_id}-sched",
            "total_assigned": assigned,
            "coverage_pct": 89.0,
            "rule_compliance_overall": 0.905,   # 0~1 스케일 → 90.5%로 정규화
            "interviewer_loads": INTERVIEWER_LOADS,
        },
        producer="scheduler",
    )
    builder.add(
        EventType.RULE_VIOLATED,
        141,
        {
            "schedule_id": f"{round_id}-sched",
            "rule": "RULE3_VERTICAL_GROUP",
            "severity": "SOFT",
            "violations": [{"team": "제3기술원", "slot": "수 15시"}],
        },
        producer="scheduler",
    )
    builder.add(
        EventType.SCHEDULE_LOCKED,
        143,
        {
            "schedule_id": f"{round_id}-sched",
            "lock_level": "LOCKED",
            "assignments_count": assigned,
            "execution_seconds": 3.2,   # 배치 알고리즘 실행 시간
        },
        producer="scheduler",
    )

    # --- 안내 ---
    for offset, recipient in (
        (143, "applicant@example.com"),
        (144, "interviewer@example.com"),
        (146, "hr@example.com"),
        (149, "team-lead@example.com"),
    ):
        builder.add(
            EventType.NOTIFICATION_SENT,
            offset,
            {
                "notification_id": str(uuid4()),
                "recipient": recipient,
                "channel": "email",
            },
            producer="notification-hub",
        )
    builder.add(
        EventType.NOTIFICATION_FAILED,
        147,
        {
            "notification_id": str(uuid4()),
            "recipient": "bounced@example.com",
            "channel": "email",
            "reason": "MAILBOX_FULL",
        },
        producer="notification-hub",
    )

    # --- 노쇼 대응 ---
    noshow = builder.add(
        EventType.NOSHOW_REPORTED,
        180,
        {"noshow_applicant_ids": NOSHOW_APPLICANTS, "reported_by": "면접관박OO"},
        producer="repair-engine",
    )
    builder.add(
        EventType.REPAIR_EXECUTED,
        186,   # 노쇼 대응 0.1h
        {
            "event_id": noshow["event_id"],
            "plan_type": "A",
            "rebooked": 2,
            "deferred": 1,
        },
        producer="repair-engine",
    )
    builder.add(
        EventType.PARTICIPANT_DEFERRED,
        192,
        {"participant_ids": NOSHOW_APPLICANTS, "defer_to": "R2026-Q4-01"},
        producer="repair-engine",
    )

    return builder.events


def build_history_events() -> list[dict]:
    """면접관 피로도(3회차 연속) 판정을 위한 과거 회차 최소 이벤트"""
    events: list[dict] = []
    for index, round_id in enumerate(HISTORY_ROUNDS):
        base = DEMO_BASE_TIME - timedelta(days=90 * (len(HISTORY_ROUNDS) - index))
        builder = _EventBuilder(round_id, base)
        builder.add(
            EventType.SCHEDULE_GENERATED,
            0,
            {
                "schedule_id": f"{round_id}-sched",
                "total_assigned": 70,
                "coverage_pct": 80.0,
                "rule_compliance_overall": 0.78,
                "interviewer_loads": HISTORY_LOADS,
            },
            producer="scheduler",
        )
        builder.add(
            EventType.SCHEDULE_LOCKED,
            30,
            {
                "schedule_id": f"{round_id}-sched",
                "lock_level": "LOCKED",
                "assignments_count": 70,
            },
            producer="scheduler",
        )
        events.extend(builder.events)
    return events


def seed_baseline(session: Session, round_id: str = BASELINE_ROUND) -> None:
    """도입 전 회차의 KPI를 kpi_snapshots에 직접 주입"""
    kpi = KpiRepository(session)
    for metric, value in BASELINE_METRICS.items():
        kpi.record(round_id, metric, value, {"source": "pre_system_baseline"})
    session.commit()


def seed_all(session: Session) -> dict:
    """baseline + 과거 회차 + 데모 회차 전체 주입"""
    from app.services.event_collector import ingest_many

    seed_baseline(session)
    history = ingest_many(session, build_history_events())
    demo = ingest_many(session, build_demo_events())

    return {
        "baseline_round": BASELINE_ROUND,
        "baseline_metrics": len(BASELINE_METRICS),
        "history_rounds": HISTORY_ROUNDS,
        "history_events": sum(1 for r in history if r.status == "stored"),
        "demo_round": DEMO_ROUND,
        "demo_events": sum(1 for r in demo if r.status == "stored"),
    }
