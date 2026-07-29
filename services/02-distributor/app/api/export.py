"""팀별 엑셀 내보내기 라우터."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db import AssignmentReasonORM, db_session
from app.services.excel_exporter import export_team_excel
from app.services.plan_service import ServiceError, get_plan

router = APIRouter(prefix="/api/v1/distribute", tags=["export"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/{plan_id}/export/{team_name}", summary="팀별 엑셀 내보내기")
def export_team(plan_id: str, team_name: str, session: Session = Depends(db_session)):
    get_plan(session, plan_id)  # 존재 확인 (없으면 NOT_FOUND)
    rows = session.scalars(
        select(AssignmentReasonORM)
        .where(
            AssignmentReasonORM.plan_id == plan_id,
            AssignmentReasonORM.team_name == team_name,
        )
        .order_by(AssignmentReasonORM.is_duplicate, AssignmentReasonORM.score.desc())
    ).all()
    if not rows:
        raise ServiceError(
            "NOT_FOUND", f"plan_id={plan_id} 의 {team_name} 배정이 없습니다", status_code=404
        )

    payload = [
        {
            "applicant_id": row.applicant_id,
            "applicant_name": row.applicant_name,
            "score": row.score,
            "tags": list(row.tags or []),
            "is_duplicate": row.is_duplicate,
            "primary_team": row.primary_team,
            "snapshot": dict(row.snapshot or {}),
        }
        for row in rows
    ]
    path = export_team_excel(plan_id, team_name, payload)
    return FileResponse(path=path, media_type=XLSX_MIME, filename=f"{team_name}.xlsx")
