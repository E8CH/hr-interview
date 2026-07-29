"""GET /api/v1/responses/{round_id} — 회신 현황"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.domain.invitee import Invitee
from app.domain.request import Request
from app.infrastructure.db import get_db
from app.schemas import ok
from app.services.response_service import compute_response_hours
from app.timeutil import to_aware_utc

router = APIRouter(prefix="/api/v1/responses", tags=["responses"])


@router.get("/{round_id}")
def get_responses(
    round_id: str,
    team: str | None = Query(default=None, description="팀 이름으로 필터"),
    org: str | None = Query(default=None, description="조직으로 필터"),
    include_payload: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Invitee)
        .join(Request, Invitee.request_id == Request.request_id)
        .filter(Request.round_id == round_id)
    )
    if team:
        query = query.filter(Invitee.team == team)
    if org:
        query = query.filter(Invitee.org == org)

    invitees = query.all()
    items = []
    hours_list: list[float] = []

    for inv in invitees:
        responded = inv.has_responded
        hours = compute_response_hours(inv) if responded else None
        if hours is not None:
            hours_list.append(hours)
        items.append(
            {
                "invitee_id": inv.invitee_id,
                "name": inv.name,
                "email": inv.email,
                "team": inv.team,
                "org": inv.org,
                "responded": responded,
                "submitted_at": to_aware_utc(inv.response.submitted_at) if responded else None,
                "response_hours": round(hours, 2) if hours is not None else None,
                "last_reminder_level": inv.last_reminder_level,
                "payload": inv.response.payload if (responded and include_payload) else None,
            }
        )

    total = len(items)
    responded_count = sum(1 for i in items if i["responded"])
    avg_hours = round(sum(hours_list) / len(hours_list), 2) if hours_list else None

    return ok(
        {
            "round_id": round_id,
            "total": total,
            "responded": responded_count,
            "pending": total - responded_count,
            "response_rate": round(responded_count / total, 4) if total else 0.0,
            "avg_response_hours": avg_hours,
            "responses": items,
        }
    )
