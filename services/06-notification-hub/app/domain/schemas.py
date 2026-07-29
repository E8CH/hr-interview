"""Pydantic 요청/응답 스키마"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ChannelType = Literal["email", "slack", "sms"]


class SendRequest(BaseModel):
    template_id: str
    channel: ChannelType = "email"
    recipient: str
    cc: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    round_id: str | None = None


class BroadcastRecipient(BaseModel):
    email: str | None = None
    recipient: str | None = None
    cc: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @property
    def address(self) -> str:
        return self.recipient or self.email or ""


class BroadcastRequest(BaseModel):
    template_id: str
    channel: ChannelType = "email"
    recipients: list[BroadcastRecipient]
    context: dict[str, Any] = Field(default_factory=dict)  # 공통 컨텍스트(개별 값이 우선)
    correlation_id: str | None = None
    round_id: str | None = None


class TemplateUpsert(BaseModel):
    channel: ChannelType = "email"
    subject: str | None = None
    body: str


class ChannelCreate(BaseModel):
    channel_id: str
    channel_type: ChannelType
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ChannelToggle(BaseModel):
    enabled: bool | None = None


class PreviewRequest(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)
