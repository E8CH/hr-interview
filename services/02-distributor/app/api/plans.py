"""배포안 라우터 — 생성 · 조회 · 승인 · 조정 · 반려."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.domain.plan import AdjustRequest, ApproveRequest, CreatePlanRequest, RejectRequest
from app.infrastructure.db import db_session
from app.services import plan_service

router = APIRouter(prefix="/api/v1/distribute", tags=["distribute"])


@router.post("/plan", status_code=status.HTTP_201_CREATED, summary="배포안 생성")
def create_plan(body: CreatePlanRequest, session: Session = Depends(db_session)):
    summary = plan_service.create_plan(
        session,
        round_id=body.round_id,
        master_version_id=body.master_version_id,
        allow_duplicate=body.allow_duplicate,
        duplicate_score_threshold=body.duplicate_score_threshold,
        created_by=body.created_by,
    )
    return {"data": summary.model_dump(mode="json"), "error": None}


@router.get("/rounds/{round_id}/plans", summary="회차의 배포안 목록 (최신순)")
def list_round_plans(round_id: str, session: Session = Depends(db_session)):
    return {"data": plan_service.list_plans_for_round(session, round_id), "error": None}


@router.delete("/rounds/{round_id}", summary="회차 배포안 비우기")
def reset_round(round_id: str, session: Session = Depends(db_session)):
    return {"data": plan_service.reset_round(session, round_id), "error": None}


@router.get("/{plan_id}", summary="배포안 상세 조회")
def get_plan(plan_id: str, session: Session = Depends(db_session)):
    return {"data": plan_service.get_plan_detail(session, plan_id), "error": None}


@router.get("/{plan_id}/applicants", summary="확정 명단 (스케줄러 입력)")
def get_plan_applicants(plan_id: str, session: Session = Depends(db_session)):
    return {"data": plan_service.get_plan_applicants(session, plan_id), "error": None}


@router.post("/{plan_id}/approve", summary="배포안 승인")
def approve(plan_id: str, body: ApproveRequest, session: Session = Depends(db_session)):
    summary = plan_service.approve_plan(session, plan_id, body.actor)
    return {"data": summary.model_dump(mode="json"), "error": None}


@router.post("/{plan_id}/adjust", summary="HR 수동 조정")
def adjust(plan_id: str, body: AdjustRequest, session: Session = Depends(db_session)):
    summary = plan_service.adjust_plan(session, plan_id, body.moves, body.actor)
    return {"data": summary.model_dump(mode="json"), "error": None}


@router.post("/{plan_id}/reject", summary="배포안 반려")
def reject(plan_id: str, body: RejectRequest, session: Session = Depends(db_session)):
    summary = plan_service.reject_plan(session, plan_id, body.reason)
    return {"data": summary.model_dump(mode="json"), "error": None}
