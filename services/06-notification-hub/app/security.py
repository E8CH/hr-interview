"""JWT 인증 — 공통 계약 §1 (Bearer Token · HS256 · 만료 8h)

PoC 단계에서는 형제 서비스들이 아직 토큰을 붙이지 않으므로 AUTH_ENABLED=false 가
기본값이다. 프로덕션 배포 시 .env 에서 true 로 올리면 모든 /api/v1 경로가 보호된다.
헬스체크(/healthz)와 메트릭(/metrics), 트래킹 픽셀은 항상 공개다
(픽셀은 메일 클라이언트가 호출하므로 인증을 걸 수 없다).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from app.responses import ApiError

ALGORITHM = "HS256"
TOKEN_TTL_HOURS = 8

_bearer = HTTPBearer(auto_error=False)


def auth_enabled() -> bool:
    return os.getenv("AUTH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "poc-dev-secret-change-me")


class UnauthorizedError(ApiError):
    def __init__(self, message: str = "인증이 필요합니다") -> None:
        super().__init__("UNAUTHORIZED", message, status_code=401)


def create_token(subject: str, **claims: Any) -> str:
    """서비스 간 호출용 토큰 발급 (테스트·로컬 도구용)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_TTL_HOURS)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, jwt_secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM])
    except JWTError as exc:
        raise UnauthorizedError(f"토큰 검증 실패: {exc}") from exc


def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict | None:
    """FastAPI 의존성 — AUTH_ENABLED=true 일 때만 강제한다."""
    if not auth_enabled():
        return None
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Authorization: Bearer <token> 헤더가 필요합니다")
    claims = decode_token(credentials.credentials)
    request.state.claims = claims
    return claims
