"""폼 응답 스키마 검증

명세의 `validate_form_response` 를 기준으로, 공통 계약의 `Day`/`Hour` 리터럴까지 대조한다.
"""
from __future__ import annotations

from typing import Any

from shared.contracts.constants import DAYS, HOURS, day_name

REQUIRED_FIELDS = ("job_role", "available_slots")
MAX_SLOTS = len(DAYS) * len(HOURS)  # 닷새 × 하루 8칸 = 40


def validate_form_response(payload: Any) -> tuple[bool, str]:
    """응답 payload 검증.

    Returns:
        (통과 여부, 사유). 통과 시 "OK".
    """
    if not isinstance(payload, dict):
        return False, "payload 형식 오류: 객체가 아님"

    for key in REQUIRED_FIELDS:
        if key not in payload:
            return False, f"필수 누락: {key}"

    job_role = payload.get("job_role")
    if not isinstance(job_role, str) or not job_role.strip():
        return False, "job_role 이 비어 있음"

    slots = payload.get("available_slots")
    if not isinstance(slots, list):
        return False, "available_slots 형식 오류: 배열이 아님"
    if not slots:
        return False, "슬롯 없음"
    if len(slots) > MAX_SLOTS:
        return False, f"슬롯 개수 초과: {len(slots)} > {MAX_SLOTS}"

    seen: set[tuple[str, str]] = set()
    for slot in slots:
        if not isinstance(slot, dict) or "day" not in slot or "hour" not in slot:
            return False, f"슬롯 형식 오류: {slot}"
        # 날은 '1일차 … 5일차'. 옛 회차가 요일('월')로 적어 보낸 것은 같은 뜻으로
        # 받아 준다 — 옛 폼 링크로 다시 내신 분의 답을 여기서 물리지 않게.
        day, hour = day_name(slot["day"]), slot["hour"]
        if day not in DAYS:
            return False, f"알 수 없는 날: {slot['day']}"
        if hour not in HOURS:
            return False, f"허용되지 않은 시간대: {hour}"
        if (day, hour) in seen:
            return False, f"중복 슬롯: {day} {hour}"
        seen.add((day, hour))

    max_daily = payload.get("max_daily")
    if max_daily is not None:
        if isinstance(max_daily, bool) or not isinstance(max_daily, int):
            return False, "max_daily 형식 오류: 정수가 아님"
        if not 1 <= max_daily <= len(HOURS):
            return False, f"max_daily 범위 오류: 1~{len(HOURS)}"

    backup = payload.get("backup_contact")
    if backup not in (None, "") and "@" not in str(backup):
        return False, f"backup_contact 형식 오류: {backup}"

    return True, "OK"


def normalize_payload(payload: dict) -> dict:
    """저장 전 정규화 — 공백 제거 · 선택 필드 기본값 채움."""
    normalized = dict(payload)
    normalized["job_role"] = str(normalized["job_role"]).strip()
    normalized["available_slots"] = [
        {"day": day_name(s["day"]), "hour": s["hour"]}
        for s in normalized["available_slots"]
    ]
    normalized.setdefault("max_daily", len(HOURS))
    normalized.setdefault("backup_contact", None)
    notes = normalized.get("notes")
    normalized["notes"] = notes.strip() if isinstance(notes, str) else None
    return normalized
