"""면접관 등록·조회·가용성 업데이트 API"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.interviewer import Interviewer
from app.domain.schemas import (
    InterviewerBandsIn,
    InterviewerCreate,
    InterviewerUpdate,
    RoundSelectionIn,
)
from app.infrastructure.contracts import band_of, normalize_availability
from app.errors import ConflictError, NotFoundError, ok
from app.infrastructure.db import get_db
from app.services import interviewer_roster

router = APIRouter(prefix="/api/v1/interviewers", tags=["interviewers"])


def _serialize(row: Interviewer) -> dict:
    availability = normalize_availability(row.availability)
    return {
        "interviewer_id": row.interviewer_id,
        "name": row.name,
        "title": row.title or "",
        "team": row.team,
        "max_daily": row.max_daily,
        "priority": row.priority,
        "email": row.email,
        "backup_email": row.backup_email,
        # 옛 시각 이름으로 저장된 자료도 지금 칸으로 맞춰 내보낸다
        "availability": availability,
        # 화면에서 고른 오전 · 오후 덩어리 (아직 안 정했으면 빈칸)
        "time_band": band_of(availability) if availability else "",
    }


@router.get("")
def list_interviewers(team: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Interviewer).order_by(Interviewer.interviewer_id)
    if team:
        stmt = stmt.where(Interviewer.team == team)
    rows = db.scalars(stmt).all()
    return ok([_serialize(r) for r in rows])


@router.post("/import", status_code=status.HTTP_201_CREATED, summary="면접관명단.xlsx 업로드")
async def import_roster(
    file: UploadFile = File(...),
    actor: str = Form("console"),
    db: Session = Depends(get_db),
):
    """사번 | 성명 | 직급 | 소속팀 | 이메일 | 일일최대 | 우선순위 컬럼을 읽어 마스터에 반영."""
    data = await file.read()
    result = interviewer_roster.import_roster(db, data)
    result["file_name"] = file.filename
    result["actor"] = actor
    return ok(result)


@router.get("/rounds/{round_id}", summary="회차에 선별된 면접관")
def list_round_interviewers(round_id: str, db: Session = Depends(get_db)):
    return ok(interviewer_roster.list_for_round(db, round_id))


@router.put("/rounds/{round_id}", summary="회차 면접관 선별 (전체 교체)")
def set_round_interviewers(
    round_id: str, body: RoundSelectionIn, db: Session = Depends(get_db)
):
    return ok(interviewer_roster.select_for_round(
        db, round_id, body.interviewer_ids, actor=body.actor
    ))


@router.put("/bands", summary="가능 시간(오전 · 오후) 일괄 저장")
def set_time_bands(body: InterviewerBandsIn, db: Session = Depends(get_db)):
    """현업이 화면에서 고른 오전 · 오후를 한 번에 저장한다.

    사번 하나씩 PUT 하면 30명이 곧 30번 호출이라, 화면에서 '정하기'를 누를 때는
    이 경로로 한 번에 보낸다.
    """
    return ok(interviewer_roster.set_bands(
        db, body.bands, actor=body.actor, timing=body.timing))


@router.get("/{interviewer_id}")
def get_interviewer(interviewer_id: str, db: Session = Depends(get_db)):
    row = db.get(Interviewer, interviewer_id)
    if row is None:
        raise NotFoundError(f"면접관을 찾을 수 없습니다: {interviewer_id}")
    return ok(_serialize(row))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_interviewer(body: InterviewerCreate, db: Session = Depends(get_db)):
    if db.get(Interviewer, body.interviewer_id) is not None:
        raise ConflictError(f"이미 등록된 면접관입니다: {body.interviewer_id}")
    row = Interviewer(**body.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return ok(_serialize(row))


@router.put("/{interviewer_id}")
def update_interviewer(interviewer_id: str, body: InterviewerUpdate, db: Session = Depends(get_db)):
    row = db.get(Interviewer, interviewer_id)
    if row is None:
        raise NotFoundError(f"면접관을 찾을 수 없습니다: {interviewer_id}")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return ok(_serialize(row))
