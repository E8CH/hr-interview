"""공개 폼 페이지 — GET /form/{token} · POST /form/{token}/submit"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request as HttpRequest
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.errors import NotFound
from app.config import SERVICE_ROOT
from app.infrastructure.db import get_db
from app.schemas import FormSubmitIn, fail, ok
from app.services import response_service
from app.services.response_service import SubmissionError
from shared.contracts.constants import DAYS, HOURS

router = APIRouter(tags=["form"])
templates = Jinja2Templates(directory=str(SERVICE_ROOT / "app" / "templates"))


def _load_invitee(db: Session, token: str):
    invitee = response_service.get_invitee_by_token(db, token)
    if invitee is None:
        raise NotFound("유효하지 않은 폼 링크입니다.")
    return invitee


@router.get("/form/{token}", response_class=HTMLResponse)
def render_form(token: str, request: HttpRequest, db: Session = Depends(get_db)):
    """폼 HTML 렌더링 (서버 사이드, 외부 리소스 없음 → 1초 이내 로딩)."""
    invitee = _load_invitee(db, token)
    response_service.mark_opened(db, invitee)

    existing = invitee.response
    return templates.TemplateResponse(
        request=request,
        name="form.html",
        context={
            "token": token,
            "invitee": invitee,
            "req": invitee.request,
            "days": DAYS,
            "hours": HOURS,
            "already_submitted": existing is not None,
            "submitted_payload": existing.payload if existing else None,
            "closed": invitee.request.is_closed,
            "max_slots": len(HOURS),
        },
    )


@router.post("/form/{token}/submit")
def submit_form(
    token: str,
    body: FormSubmitIn,
    db: Session = Depends(get_db),
):
    """응답 제출 — 검증 통과 시 RESPONSE_RECEIVED 발행."""
    invitee = _load_invitee(db, token)
    payload = body.model_dump(exclude_none=False)
    payload["available_slots"] = [
        {"day": s.day, "hour": s.hour} for s in body.available_slots
    ]

    try:
        response = response_service.submit_response(db, invitee, payload)
    except SubmissionError as exc:
        status_code = 409 if exc.code in {"ALREADY_SUBMITTED", "REQUEST_CLOSED"} else 422
        return JSONResponse(status_code=status_code, content=fail(exc.code, exc.message))

    return ok(
        {
            "response_id": response.response_id,
            "invitee_id": invitee.invitee_id,
            "submitted_at": response.submitted_at.isoformat(),
            "validated": response.validated,
            "slot_count": response.slot_count,
            "response_hours": response_service.compute_response_hours(invitee),
        }
    )
