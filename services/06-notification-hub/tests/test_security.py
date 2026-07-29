"""JWT 인증 — 공통 계약 §1 (Bearer · HS256 · 8h)"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.security import (
    ALGORITHM,
    TOKEN_TTL_HOURS,
    UnauthorizedError,
    auth_enabled,
    create_token,
    decode_token,
    jwt_secret,
)


def test_auth_disabled_by_default_in_poc():
    assert auth_enabled() is False


def test_create_and_decode_token():
    token = create_token("service-03", role="internal")
    claims = decode_token(token)
    assert claims["sub"] == "service-03"
    assert claims["role"] == "internal"

    ttl = claims["exp"] - claims["iat"]
    assert ttl == TOKEN_TTL_HOURS * 3600  # 만료 8h


def test_decode_rejects_tampered_token():
    token = create_token("service-03")
    with pytest.raises(UnauthorizedError):
        decode_token(token + "x")


def test_decode_rejects_wrong_secret(monkeypatch):
    token = create_token("service-03")
    monkeypatch.setenv("JWT_SECRET", "다른-비밀키")
    with pytest.raises(UnauthorizedError):
        decode_token(token)


def test_decode_rejects_expired_token():
    now = datetime.now(timezone.utc) - timedelta(hours=TOKEN_TTL_HOURS + 1)
    expired = jwt.encode(
        {
            "sub": "service-03",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=1)).timestamp()),
        },
        jwt_secret(),
        algorithm=ALGORITHM,
    )
    with pytest.raises(UnauthorizedError):
        decode_token(expired)


def test_endpoints_open_when_auth_disabled(client):
    assert client.get("/api/v1/notify/templates").status_code == 200


def test_protected_endpoints_require_token(client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")

    response = client.get("/api/v1/notify/templates")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"

    bad = client.get(
        "/api/v1/notify/templates", headers={"Authorization": "Bearer not-a-token"}
    )
    assert bad.status_code == 401

    good = client.get(
        "/api/v1/notify/templates",
        headers={"Authorization": f"Bearer {create_token('service-03')}"},
    )
    assert good.status_code == 200
    assert good.json()["data"]["count"] == 10


def test_send_requires_token_when_enabled(client, invite_context, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    payload = {
        "template_id": "invite",
        "channel": "email",
        "recipient": "iv1@lge.com",
        "context": invite_context,
    }
    assert client.post("/api/v1/notify/send", json=payload).status_code == 401
    authorized = client.post(
        "/api/v1/notify/send",
        json=payload,
        headers={"Authorization": f"Bearer {create_token('service-03')}"},
    )
    assert authorized.status_code == 202


def test_public_endpoints_stay_open_when_auth_enabled(
    client, session, invite_context, monkeypatch
):
    """헬스체크·메트릭·트래킹 픽셀은 인증을 걸지 않는다."""
    from app.services.dispatcher import deliver, enqueue

    notification = enqueue(
        session,
        template_id="invite",
        channel="email",
        recipient="open@lge.com",
        context=invite_context,
    )
    deliver(session, notification)
    session.commit()

    monkeypatch.setenv("AUTH_ENABLED", "true")
    assert client.get("/healthz").status_code == 200
    assert client.get("/metrics").status_code == 200
    assert client.get("/").status_code == 200

    pixel = client.get(
        f"/api/v1/notify/track/open/{notification.notification_id}.png"
    )
    assert pixel.status_code == 200
    session.expire_all()
    from app.domain.models import Notification

    assert session.get(Notification, notification.notification_id).opened_at is not None
