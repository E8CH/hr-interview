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
from shared.contracts.constants import (
    BAND_ALL,
    BAND_BACK,
    BAND_FRONT,
    BAND_NONE,
    DAYS,
    HOURS,
    band_hours,
    band_of,
)

router = APIRouter(tags=["form"])
templates = Jinja2Templates(directory=str(SERVICE_ROOT / "app" / "templates"))

#: 폼에서 고를 수 있는 것 — 어느 날인지는 묻지 않는다. 고른 덩어리가 모든 날에 그대로 간다.
BAND_CHOICES = [BAND_ALL, BAND_FRONT, BAND_BACK]


def _bands(timing=None) -> list[dict]:
    """폼에 그릴 덩어리 목록 — 이름 · 실제 칸 · 설명 한 줄."""
    out = []
    for band in BAND_CHOICES:
        hours = band_hours(band, timing)
        if not hours:
            continue
        out.append({
            "name": band,
            "hours": hours,
            "help": f"{hours[0]} ~ {hours[-1]} · 하루 {len(hours)}칸 (날은 가리지 않습니다)",
        })
    return out


def _submitted_band(payload, timing=None) -> str:
    """이미 낸 응답이 어느 덩어리였는지 되읽는다 — 다시 열었을 때 눌러 두려고."""
    if not payload:
        return ""
    availability: dict[str, list[str]] = {}
    for slot in payload.get("available_slots") or []:
        day = slot.get("day") if isinstance(slot, dict) else getattr(slot, "day", None)
        hour = slot.get("hour") if isinstance(slot, dict) else getattr(slot, "hour", None)
        if day and hour:
            availability.setdefault(day, []).append(hour)
    band = band_of(availability, timing)
    return "" if band == BAND_NONE else band


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
            "bands": _bands(),
            "already_submitted": existing is not None,
            "submitted_payload": existing.payload if existing else None,
            "submitted_band": _submitted_band(existing.payload if existing else None),
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
