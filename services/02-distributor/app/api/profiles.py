"""팀 프로필 라우터."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.domain.profile import TeamProfileUpdate
from app.infrastructure.db import TeamProfileORM, db_session, load_profiles, utcnow
from app.services.plan_service import ServiceError

router = APIRouter(prefix="/api/v1/profiles", tags=["profiles"])


@router.get("", summary="팀 프로필 전체 조회")
def list_profiles(session: Session = Depends(db_session)):
    profiles = [p.model_dump(mode="json") for p in load_profiles(session)]
    return {"data": profiles, "error": None}


@router.get("/{team_name}", summary="팀 프로필 단건 조회")
def get_profile(team_name: str, session: Session = Depends(db_session)):
    row = session.get(TeamProfileORM, team_name)
    if row is None:
        raise ServiceError("NOT_FOUND", f"team_name={team_name} 없음", status_code=404)
    return {"data": row.to_domain().model_dump(mode="json"), "error": None}


@router.put("/{team_name}", summary="팀 프로필 갱신")
def update_profile(
    team_name: str, body: TeamProfileUpdate, session: Session = Depends(db_session)
):
    row = session.get(TeamProfileORM, team_name)
    if row is None:
        raise ServiceError("NOT_FOUND", f"team_name={team_name} 없음", status_code=404)
    row.primary_job = body.primary_job
    row.secondary_job = body.secondary_job
    row.preferred_majors = body.preferred_majors
    row.org_allowed = body.org_allowed
    row.grad_ratio_target = body.grad_ratio_target
    row.target_headcount = body.target_headcount
    row.special_tags = body.special_tags
    row.updated_at = utcnow()
    session.commit()
    return {"data": row.to_domain().model_dump(mode="json"), "error": None}
