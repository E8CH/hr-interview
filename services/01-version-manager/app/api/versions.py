"""버전 관리 API 라우터 (/api/v1/versions)."""
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.infrastructure.db import get_session
from app.schemas import RollbackRequest, ok
from app.services import version_service as svc

router = APIRouter(prefix="/api/v1/versions", tags=["versions"])


@router.post("/register", status_code=201)
async def register(
    file: UploadFile = File(...),
    round_id: str = Form(...),
    kind: str = Form(...),
    actor: str = Form(...),
    team_name: str | None = Form(None),
    session: Session = Depends(get_session),
):
    data = await file.read()
    version = svc.register_version(
        session,
        file_bytes=data,
        file_name=file.filename or "upload.xlsx",
        round_id=round_id,
        kind=kind,
        actor=actor,
        team_name=team_name,
    )
    return ok({
        "version_id": version.version_id,
        "fingerprint": version.fingerprint,
        "applicant_count": version.applicant_count,
        "created_at": version.created_at,
    })


@router.get("/diff")
def diff(
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    session: Session = Depends(get_session),
):
    return ok(svc.diff(session, from_, to))


@router.get("/{round_id}")
def get_latest(
    round_id: str,
    kind: str = Query("master"),
    team_name: str | None = Query(None),
    session: Session = Depends(get_session),
):
    version = svc.get_active(session, round_id, kind, team_name)
    if version is None:
        raise svc.VersionError("NOT_FOUND", "no active version found", status_code=404)
    return ok({
        "version_id": version.version_id,
        "fingerprint": version.fingerprint,
        "applicant_count": version.applicant_count,
        "actor": version.actor,
        "created_at": version.created_at,
    })


@router.post("/verify/{round_id}")
def verify(round_id: str, session: Session = Depends(get_session)):
    return ok(svc.verify_round(session, round_id))


@router.get("/{round_id}/history")
def history(round_id: str, session: Session = Depends(get_session)):
    versions = svc.get_history(session, round_id)
    return ok([
        {
            "version_id": v.version_id,
            "kind": v.kind,
            "team_name": v.team_name,
            "fingerprint": v.fingerprint,
            "applicant_count": v.applicant_count,
            "actor": v.actor,
            "parent_version": v.parent_version,
            "is_active": v.is_active,
            "created_at": v.created_at,
        }
        for v in versions
    ])


@router.post("/rollback")
def rollback(body: RollbackRequest, session: Session = Depends(get_session)):
    version = svc.rollback(session, body.version_id)
    return ok({"restored_version_id": version.version_id})
