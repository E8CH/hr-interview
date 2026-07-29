"""GET /api/v1/patterns/organizations — 조직별 응답 패턴"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.infrastructure.db import get_db
from app.schemas import ok
from app.services import pattern_learner
from app.timeutil import to_aware_utc

router = APIRouter(prefix="/api/v1/patterns", tags=["patterns"])


@router.get("/organizations")
def list_org_patterns(db: Session = Depends(get_db)):
    rows = pattern_learner.list_patterns(db)
    return ok(
        [
            {
                "org": r.org,
                "mean_hours": round(r.mean_hours, 2),
                "std_hours": round(r.std_hours, 2),
                "sample_count": r.sample_count,
                "predicted_slow": r.predicted_slow,
                "updated_at": to_aware_utc(r.updated_at),
            }
            for r in rows
        ]
    )


@router.get("/organizations/{org}")
def get_org_pattern(org: str, db: Session = Depends(get_db)):
    from app.api.errors import NotFound
    from app.domain.org_pattern import OrgPattern

    row = db.get(OrgPattern, org)
    if row is None:
        raise NotFound(f"학습된 패턴이 없습니다: {org}")

    return ok(
        {
            "org": row.org,
            "mean_hours": round(row.mean_hours, 2),
            "std_hours": round(row.std_hours, 2),
            "sample_count": row.sample_count,
            "predicted_slow": row.predicted_slow,
            "predicted_delay_hours": pattern_learner.predict_delay_hours(db, org),
            "updated_at": to_aware_utc(row.updated_at),
        }
    )
