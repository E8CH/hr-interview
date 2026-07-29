"""템플릿 관리 API"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Template, utcnow
from app.domain.schemas import PreviewRequest, TemplateUpsert
from app.infrastructure.db import get_db
from app.responses import NotFoundError, ValidationFailed, ok
from app.security import require_auth
from app.services.template_renderer import TemplateRenderError, get_renderer

router = APIRouter(
    prefix="/api/v1/notify", tags=["templates"], dependencies=[Depends(require_auth)]
)


def _get_or_404(session: Session, template_id: str) -> Template:
    template = session.get(Template, template_id)
    if template is None:
        raise NotFoundError(f"템플릿을 찾을 수 없습니다: {template_id}")
    return template


@router.get("/templates")
def list_templates(session: Session = Depends(get_db)):
    rows = (
        session.execute(select(Template).order_by(Template.template_id)).scalars().all()
    )
    return ok({"count": len(rows), "items": [row.to_dict() for row in rows]})


@router.get("/templates/{template_id}")
def get_template(template_id: str, session: Session = Depends(get_db)):
    template = _get_or_404(session, template_id)
    data = template.to_dict()
    renderer = get_renderer()
    data["variables"] = renderer.required_variables(
        (template.subject or "") + "\n" + template.body
    )
    return ok(data)


@router.put("/templates/{template_id}")
def upsert_template(
    template_id: str, payload: TemplateUpsert, session: Session = Depends(get_db)
):
    renderer = get_renderer()
    try:  # 문법 오류를 저장 전에 걸러낸다
        renderer.env.parse(payload.body)
        if payload.subject:
            renderer.env.parse(payload.subject)
    except Exception as exc:
        raise ValidationFailed(f"템플릿 문법 오류: {exc}") from exc

    template = session.get(Template, template_id)
    created = template is None
    if template is None:
        template = Template(template_id=template_id, channel=payload.channel, body=payload.body)
        session.add(template)
    template.channel = payload.channel
    template.subject = payload.subject
    template.body = payload.body
    template.updated_at = utcnow()
    session.commit()
    return ok({**template.to_dict(), "created": created})


@router.post("/templates/{template_id}/preview")
def preview_template(
    template_id: str, payload: PreviewRequest, session: Session = Depends(get_db)
):
    """저장하지 않고 렌더링 결과만 확인한다."""
    template = _get_or_404(session, template_id)
    try:
        subject, body = get_renderer().render(template, payload.context)
    except TemplateRenderError as exc:
        raise ValidationFailed(str(exc)) from exc
    return ok({"template_id": template_id, "subject": subject, "body": body})
