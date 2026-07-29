"""채널 관리 API — 자격증명은 이 서비스 밖으로 나가지 않는다"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Channel
from app.domain.schemas import ChannelCreate, ChannelToggle
from app.infrastructure.db import get_db
from app.responses import NotFoundError, ValidationFailed, ok
from app.security import require_auth
from app.services.channels import ADAPTERS_BY_NAME

router = APIRouter(
    prefix="/api/v1/notify", tags=["channels"], dependencies=[Depends(require_auth)]
)

_SECRET_KEYS = {"api_key", "password", "token", "secret", "webhook_url"}


def _mask(config: dict[str, Any] | None) -> dict[str, Any]:
    """자격증명 필드는 마스킹해서 응답한다."""
    masked: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if key in _SECRET_KEYS and value:
            text = str(value)
            masked[key] = f"***{text[-4:]}" if len(text) > 4 else "***"
        else:
            masked[key] = value
    return masked


def _view(channel: Channel) -> dict:
    data = channel.to_dict()
    data["config"] = _mask(channel.config)
    return data


@router.get("/channels")
def list_channels(session: Session = Depends(get_db)):
    rows = session.execute(select(Channel).order_by(Channel.channel_id)).scalars().all()
    return ok(
        {
            "count": len(rows),
            "items": [_view(row) for row in rows],
            "adapters": sorted(ADAPTERS_BY_NAME),
        }
    )


@router.post("/channels", status_code=201)
def create_channel(payload: ChannelCreate, session: Session = Depends(get_db)):
    adapter_name = payload.config.get("adapter")
    if adapter_name and adapter_name not in ADAPTERS_BY_NAME:
        raise ValidationFailed(
            f"알 수 없는 어댑터: {adapter_name} (사용 가능: {sorted(ADAPTERS_BY_NAME)})"
        )
    existing = session.get(Channel, payload.channel_id)
    if existing is not None:
        raise ValidationFailed(f"이미 존재하는 채널입니다: {payload.channel_id}")
    channel = Channel(
        channel_id=payload.channel_id,
        channel_type=payload.channel_type,
        config=payload.config,
        enabled=payload.enabled,
    )
    session.add(channel)
    session.commit()
    return ok(_view(channel))


@router.put("/channels/{channel_id}/toggle")
def toggle_channel(
    channel_id: str,
    payload: ChannelToggle | None = None,
    session: Session = Depends(get_db),
):
    channel = session.get(Channel, channel_id)
    if channel is None:
        raise NotFoundError(f"채널을 찾을 수 없습니다: {channel_id}")
    if payload is not None and payload.enabled is not None:
        channel.enabled = payload.enabled
    else:
        channel.enabled = not channel.enabled
    session.commit()
    return ok(_view(channel))
