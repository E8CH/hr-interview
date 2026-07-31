"""노쇼 · 취소 통보 진입점"""
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.schemas import CancelRequest, NoshowRequest, ok
from app.infrastructure.db import get_session
from app.services import repair_service

router = APIRouter(prefix="/api/v1/repair", tags=["repair"])


@router.delete("/rounds/{round_id}", summary="회차 재편성 기록 비우기")
def reset_round(round_id: str, session: Session = Depends(get_session)):
    return ok(repair_service.reset_round(session, round_id))


@router.post("/noshow", status_code=status.HTTP_202_ACCEPTED)
def report_noshow(payload: NoshowRequest, response: Response,
                  session: Session = Depends(get_session)):
    result = repair_service.report_noshow(
        session,
        round_id=payload.round_id,
        schedule_id=payload.schedule_id,
        noshow_applicant_ids=payload.noshow_applicant_ids,
        reported_by=payload.reported_by,
    )
    response.status_code = status.HTTP_202_ACCEPTED
    return ok(result)


@router.post("/cancel", status_code=status.HTTP_202_ACCEPTED)
def report_cancel(payload: CancelRequest, response: Response,
                  session: Session = Depends(get_session)):
    result = repair_service.report_cancel(
        session,
        round_id=payload.round_id,
        schedule_id=payload.schedule_id,
        cancel_type=payload.cancel_type,
        target_id=payload.target_id,
        reason=payload.reason,
    )
    response.status_code = status.HTTP_202_ACCEPTED
    return ok(result)
