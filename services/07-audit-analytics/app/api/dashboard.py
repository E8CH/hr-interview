"""실시간 KPI 대시보드 API (v2 대시보드 백엔드)

이벤트가 없는 회차도 200 + 0값으로 응답한다 — 대시보드가 렌더링 도중
깨지지 않도록 하는 것이 이 엔드포인트들의 계약이다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.responses import ok
from app.domain.kpi import Metric, temperature_of
from app.infrastructure.db import session_scope
from app.infrastructure.repository import KpiRepository, OrgStatRepository
from app.services.report_generator import round_metric
from app.services.risk_detector import detect_risks, overall_risk_level

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


def build_kpi(session: Session, round_id: str) -> dict:
    kpi = KpiRepository(session)
    risks = detect_risks(session, round_id)

    def value(metric: str, default: float = 0.0) -> float:
        result = kpi.latest_value(round_id, metric, default)
        return default if result is None else result

    return {
        "총_대상자": int(value(Metric.TOTAL_TARGETS)),
        "회신_완료": int(value(Metric.RESPONSE_DONE)),
        "회신_대기": int(value(Metric.RESPONSE_PENDING)),
        "배치_완료율": round_metric(value(Metric.ASSIGN_RATE)) or 0.0,
        "규칙_준수율": round_metric(value(Metric.RULE_COMPLIANCE)) or 0.0,
        "위험도": overall_risk_level(risks),
        "실행_시간_초": round_metric(value(Metric.EXEC_SECONDS)) or 0.0,
    }


def build_organizations(session: Session, round_id: str) -> list[dict]:
    return [
        {
            "org": stat.org,
            "avg_response_h": round_metric(stat.mean_hours) or 0.0,
            "completion": round_metric(stat.completion_rate) or 0.0,
            "temperature": temperature_of(stat.mean_hours),
            "predicted_slow": bool(stat.predicted_slow),
        }
        for stat in OrgStatRepository(session).by_round(round_id)
    ]


@router.get("/kpi")
def get_kpi(
    round_id: str = Query(..., description="회차 ID"),
    session: Session = Depends(session_scope),
):
    return ok(build_kpi(session, round_id))


@router.get("/organizations")
def get_organizations(
    round_id: str = Query(..., description="회차 ID"),
    session: Session = Depends(session_scope),
):
    """조직별 협업 온도계"""
    return ok(build_organizations(session, round_id))


@router.get("/risks")
def get_risks(
    round_id: str = Query(..., description="회차 ID"),
    session: Session = Depends(session_scope),
):
    """위험 신호 감지 4종"""
    return ok([signal.to_response() for signal in detect_risks(session, round_id)])
