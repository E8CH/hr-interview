"""초대 · 리마인더 메시지 본문 생성 (톤별 문구)"""
from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.services.reminder_engine import rule_for_level


def form_url(token: str) -> str:
    return f"{settings.form_base_url}/form/{token}"


def _deadline_text(deadline: datetime) -> str:
    return deadline.strftime("%Y-%m-%d %H:%M UTC")


def invitation(name: str, team: str, deadline: datetime, token: str) -> tuple[str, str]:
    """초대 메일 (제목, 본문)."""
    subject = f"[면접 일정 회신 요청] {team} {name}님 — {_deadline_text(deadline)}까지"
    body = (
        f"{name}님, 안녕하세요.\n\n"
        f"{team} 면접위원으로 배정되어 가능 시간대 회신을 요청드립니다.\n"
        f"아래 링크의 폼에서 가능한 시간대(앞타임 · 뒤타임 · 모든타임) 하나를\n"
        f"고른 뒤 제출해 주세요. 날은 묻지 않습니다.\n\n"
        f"  {form_url(token)}\n\n"
        f"회신 마감: {_deadline_text(deadline)}\n"
        f"소요 시간: 약 1분\n\n"
        f"감사합니다.\nHR 면접 운영팀 드림"
    )
    return subject, body


_TONE_BODY = {
    1: (
        "{name}님, 안녕하세요.\n\n"
        "앞서 요청드린 면접 가능 시간 회신이 아직 접수되지 않아 정중히 다시 안내드립니다.\n"
        "잠시만 시간 내어 아래 폼을 제출해 주시면 감사하겠습니다.\n\n"
        "  {url}\n\n"
        "회신 마감: {deadline}\n"
    ),
    2: (
        "{name}님, 안녕하세요.\n\n"
        "[마감 임박] 면접 가능 시간 회신 마감이 얼마 남지 않았습니다.\n"
        "미회신 시 시간표 편성에서 제외될 수 있으니 아래 폼을 꼭 제출해 주세요.\n\n"
        "  {url}\n\n"
        "회신 마감: {deadline}\n"
    ),
    3: (
        "{name}님, 안녕하세요.\n\n"
        "[최종 알림] 면접 가능 시간 회신이 끝내 접수되지 않아 부서장님을 참조로 안내드립니다.\n"
        "본 메일 이후로는 자동 리마인더가 발송되지 않으며, 시간표는 현재 회신분으로 편성됩니다.\n\n"
        "  {url}\n\n"
        "회신 마감: {deadline}\n"
    ),
}


def reminder(name: str, level: int, deadline: datetime, token: str) -> tuple[str, str]:
    """리마인더 메일 (제목, 본문)."""
    rule = rule_for_level(level)
    subject = f"[리마인더 {level}단계 · {rule['tone']}] 면접 가능 시간 회신 요청 — {name}님"
    body = _TONE_BODY[level].format(
        name=name, url=form_url(token), deadline=_deadline_text(deadline)
    )
    return subject, body
