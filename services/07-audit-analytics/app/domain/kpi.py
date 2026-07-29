"""KPI 도메인 — 메트릭 이름 상수 · 협업 온도 · 위험도 판정"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Metric:
    """kpi_snapshots.metric_name 상수.

    대시보드 노출용 지표는 명세의 한글 키를 그대로 쓴다 (v2 대시보드 호환).
    """

    # --- /dashboard/kpi 노출 지표 ---
    TOTAL_TARGETS = "총_대상자"
    RESPONSE_DONE = "회신_완료"
    RESPONSE_PENDING = "회신_대기"
    ASSIGN_RATE = "배치_완료율"
    RULE_COMPLIANCE = "규칙_준수율"
    EXEC_SECONDS = "실행_시간_초"

    # --- Before/After 비교 지표 ---
    RESPONSE_LEADTIME_H = "회신_소요시간_h"
    RESPONSE_COMPLETION = "회신_완료율"
    ASSIGN_DURATION_H = "배치_소요_h"
    NOSHOW_RESPONSE_H = "노쇼_대응_시간_h"

    # --- 위험도 (이벤트 도착 시점의 스냅샷 이력) ---
    RISK_LEVEL = "위험도"

    # --- 내부 집계 지표 ---
    NOSHOW_RESPONSE_TOTAL_H = "노쇼_대응_총시간_h"
    INVITED_TOTAL = "요청_대상자"
    ASSIGNED_TOTAL = "배치_완료건수"
    NOSHOW_COUNT = "노쇼_건수"
    REPAIR_COUNT = "재편성_건수"
    DEFERRED_COUNT = "연기_건수"
    RULE_VIOLATION_COUNT = "규칙_위반_건수"
    INTEGRITY_VIOLATION_COUNT = "무결성_위반_건수"
    ESCALATION_COUNT = "에스컬레이션_건수"
    REMINDER_COUNT = "리마인더_발송_건수"
    NOTIFY_SENT_COUNT = "알림_성공_건수"
    NOTIFY_FAILED_COUNT = "알림_실패_건수"
    PLAN_CREATED_COUNT = "배포안_생성_건수"
    DISTRIBUTION_ADJUST_COUNT = "배포_조정_건수"
    TEAM_DISTRIBUTION_COUNT = "팀별_배포_등록_건수"


# Before/After 리포트 지표 정의: (메트릭, 델타 표기 방식)
# 시간/소요 지표는 변화율(%), 비율 지표는 퍼센트포인트(pp)로 표기한다.
BEFORE_AFTER_METRICS: list[tuple[str, Literal["pct", "pp"]]] = [
    (Metric.RESPONSE_LEADTIME_H, "pct"),
    (Metric.RESPONSE_COMPLETION, "pp"),
    (Metric.ASSIGN_DURATION_H, "pct"),
    (Metric.RULE_COMPLIANCE, "pp"),
    (Metric.NOSHOW_RESPONSE_H, "pct"),
]


# --- 협업 온도계 ------------------------------------------------------------
# 명세 예시: 6h → cool, 52h → hot. 위험 룰 "조직_회신_지연: mean_hours > 40"과 정합.
TEMPERATURE_COOL_MAX_H = 12.0
TEMPERATURE_WARM_MAX_H = 40.0

Temperature = Literal["cool", "warm", "hot"]


def temperature_of(mean_hours: float | None) -> Temperature:
    """평균 응답시간 → 협업 온도"""
    if mean_hours is None:
        return "cool"
    if mean_hours <= TEMPERATURE_COOL_MAX_H:
        return "cool"
    if mean_hours <= TEMPERATURE_WARM_MAX_H:
        return "warm"
    return "hot"


# --- 위험도 ----------------------------------------------------------------
RiskLevel = Literal["Low", "Medium", "High"]
Severity = Literal["low", "medium", "high"]

_SEVERITY_WEIGHT: dict[str, int] = {"low": 1, "medium": 2, "high": 3}


def severity_weight(severity: str) -> int:
    return _SEVERITY_WEIGHT.get(severity, 0)


def risk_level_from(severities: list[str]) -> RiskLevel:
    """위험 신호 severity 목록 → 회차 종합 위험도.

    high가 하나라도 있으면 High, medium이 있으면 Medium, 그 외 Low.
    """
    if not severities:
        return "Low"
    top = max(severity_weight(s) for s in severities)
    if top >= 3:
        return "High"
    if top == 2:
        return "Medium"
    return "Low"


# --- API 응답 모델 -----------------------------------------------------------


class DashboardKpi(BaseModel):
    """GET /api/v1/dashboard/kpi 의 data 필드"""

    총_대상자: int = 0
    회신_완료: int = 0
    회신_대기: int = 0
    배치_완료율: float = 0.0
    규칙_준수율: float = 0.0
    위험도: str = "Low"
    실행_시간_초: float = 0.0


class OrgTemperature(BaseModel):
    """GET /api/v1/dashboard/organizations 의 항목"""

    org: str
    avg_response_h: float
    completion: float
    temperature: str
    predicted_slow: bool = False


class RiskSignal(BaseModel):
    """GET /api/v1/dashboard/risks 의 항목.

    명세 예시가 항목마다 필드가 다르므로(team/count/interviewer)
    공통 필드 외에는 None인 키를 응답에서 제외한다.
    """

    type: str
    severity: str
    team: str | None = None
    count: int | None = None
    interviewer: str | None = None
    detail: str | None = None

    def to_response(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}
