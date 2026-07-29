"""조직별 응답 패턴 학습

응답이 제출될 때마다 `org_patterns` 를 증분 갱신한다.
평균/표준편차는 Welford 온라인 알고리즘으로 계산하며, 저장된 (mean, std, count) 만으로
M2 = std² × count 를 복원할 수 있어 별도 컬럼 없이 정확한 증분 갱신이 가능하다.
(표준편차는 모집단 기준)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import structlog
from sqlalchemy.orm import Session

from app.domain.org_pattern import SLOW_THRESHOLD_HOURS, OrgPattern
from app.timeutil import utcnow

logger = structlog.get_logger(__name__)

UNKNOWN_ORG = "미지정"


@dataclass(frozen=True)
class PatternStats:
    """조직 응답 패턴 통계 (순수 계산 결과)."""

    mean_hours: float
    std_hours: float
    sample_count: int

    @property
    def predicted_slow(self) -> bool:
        return self.mean_hours > SLOW_THRESHOLD_HOURS


def compute_stats(hours: Iterable[float]) -> PatternStats:
    """응답 소요시간 목록 → 평균/표준편차(모집단)/표본수."""
    values = [float(h) for h in hours]
    n = len(values)
    if n == 0:
        return PatternStats(0.0, 0.0, 0)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return PatternStats(mean, math.sqrt(variance), n)


def merge_stats(current: PatternStats, new_hours: float) -> PatternStats:
    """기존 통계에 관측치 1건을 증분 반영 (Welford)."""
    n_old = current.sample_count
    if n_old <= 0:
        return PatternStats(float(new_hours), 0.0, 1)

    m2_old = (current.std_hours**2) * n_old
    n = n_old + 1
    delta = new_hours - current.mean_hours
    mean = current.mean_hours + delta / n
    m2 = m2_old + delta * (new_hours - mean)
    # 부동소수 오차로 음수가 되는 것을 방지
    return PatternStats(mean, math.sqrt(max(m2, 0.0) / n), n)


def _to_stats(row: OrgPattern) -> PatternStats:
    return PatternStats(row.mean_hours, row.std_hours, row.sample_count)


def _apply(row: OrgPattern, stats: PatternStats) -> OrgPattern:
    row.mean_hours = stats.mean_hours
    row.std_hours = stats.std_hours
    row.sample_count = stats.sample_count
    row.predicted_slow = stats.predicted_slow
    row.updated_at = utcnow()
    return row


def record_response(db: Session, org: str | None, response_hours: float) -> OrgPattern:
    """응답 1건을 조직 패턴에 반영. 커밋은 호출자 책임."""
    key = (org or UNKNOWN_ORG).strip() or UNKNOWN_ORG
    row = db.get(OrgPattern, key)
    if row is None:
        row = OrgPattern(org=key)
        db.add(row)
        stats = PatternStats(float(response_hours), 0.0, 1)
    else:
        stats = merge_stats(_to_stats(row), float(response_hours))

    _apply(row, stats)
    db.flush()
    logger.info(
        "org_pattern_updated",
        org=key,
        mean_hours=round(row.mean_hours, 2),
        sample_count=row.sample_count,
        predicted_slow=row.predicted_slow,
    )
    return row


def learn_from_history(db: Session, records: Iterable[tuple[str | None, float]]) -> dict[str, OrgPattern]:
    """(org, hours) 히스토리 다건을 일괄 학습. 커밋은 호출자 책임."""
    result: dict[str, OrgPattern] = {}
    for org, hours in records:
        row = record_response(db, org, hours)
        result[row.org] = row
    return result


def list_patterns(db: Session) -> list[OrgPattern]:
    """조직 패턴 전체 — 평균 응답시간 내림차순(느린 조직 우선)."""
    rows = db.query(OrgPattern).all()
    return sorted(rows, key=lambda r: r.mean_hours, reverse=True)


def predict_delay_hours(db: Session, org: str | None) -> float | None:
    """해당 조직의 예상 응답 지연(시간). 학습 이력이 없으면 None."""
    key = (org or UNKNOWN_ORG).strip() or UNKNOWN_ORG
    row = db.get(OrgPattern, key)
    if row is None or row.sample_count == 0:
        return None
    return row.mean_hours
