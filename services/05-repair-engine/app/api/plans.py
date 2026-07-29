"""대안 시나리오 조회 · 선택"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import SelectPlanRequest, ok
from app.infrastructure.db import get_session
from app.services import repair_service

router = APIRouter(prefix="/api/v1/repair", tags=["plans"])


@router.get("/plans/{event_id}")
def get_plans(event_id: str, session: Session = Depends(get_session)):
    return ok(repair_service.get_plans(session, event_id))


@router.post("/plans/{event_id}/select")
def select_plan(event_id: str, payload: SelectPlanRequest,
                session: Session = Depends(get_session)):
    return ok(repair_service.select_plan(
        session, event_id, payload.plan_id, payload.selected_by))
