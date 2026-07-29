"""POST /api/v1/requests — 초대 요청 발송"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.infrastructure.db import get_db
from app.schemas import CreateRequestIn, ok
from app.services import request_service

router = APIRouter(prefix="/api/v1/requests", tags=["requests"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_request(body: CreateRequestIn, db: Session = Depends(get_db)):
    request, sent_count = request_service.create_request(
        db,
        round_id=body.round_id,
        plan_id=body.plan_id,
        deadline=body.deadline,
        invitees=body.invitees,
        team_name=body.team_name,
        correlation_id=body.correlation_id,
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=ok({"request_id": request.request_id, "sent_count": sent_count}),
    )


@router.get("/{request_id}")
def get_request(request_id: str, db: Session = Depends(get_db)):
    from app.api.errors import NotFound
    from app.domain.request import Request

    request = db.get(Request, request_id)
    if request is None:
        raise NotFound(f"요청을 찾을 수 없습니다: {request_id}")

    return ok(
        {
            "request_id": request.request_id,
            "round_id": request.round_id,
            "plan_id": request.plan_id,
            "team_name": request.team_name,
            "sent_at": request.sent_at,
            "deadline": request.deadline,
            "status": request.status,
            "invitee_count": len(request.invitees),
            "responded_count": sum(1 for i in request.invitees if i.has_responded),
        }
    )


@router.post("/{request_id}/close")
def close_request(request_id: str, db: Session = Depends(get_db)):
    from app.api.errors import NotFound
    from app.domain.request import Request

    request = db.get(Request, request_id)
    if request is None:
        raise NotFound(f"요청을 찾을 수 없습니다: {request_id}")

    request_service.close_request(db, request)
    return ok({"request_id": request.request_id, "status": request.status})
