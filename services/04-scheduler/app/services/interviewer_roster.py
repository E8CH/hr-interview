"""면접관명단.xlsx 업로드 · 회차별 선별.

콘솔 3번 메뉴(면접 담당자 선별)가 쓰는 경로다.
  - 업로드: 사번 | 성명 | 소속팀 | 이메일 | 일일최대 | 우선순위 를 마스터에 반영
  - 선별: 회차마다 그중 누구를 투입할지 골라 round_interviewers 에 저장

가용성(어느 요일 몇 시)은 여기서 다루지 않는다. 그건 03(회신 수집)이 모으고
04는 스케줄을 짤 때 회차 단위로 받아온다.
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.interviewer import Interviewer
from app.domain.round_interviewer import RoundInterviewer
from app.errors import ValidationFailed

# 엑셀 컬럼명 → 모델 필드. 운영에서 쓰는 표기 흔들림을 흡수한다.
COLUMN_ALIASES = {
    "interviewer_id": ("사번", "사원번호", "면접관ID", "interviewer_id"),
    "name": ("성명", "이름", "한글성명", "name"),
    "team": ("소속팀", "팀", "소속", "team"),
    "email": ("이메일", "메일", "email"),
    "max_daily": ("일일최대", "하루최대", "max_daily"),
    "priority": ("우선순위", "priority"),
}
REQUIRED = ("interviewer_id", "team")
HEADER_SCAN_ROWS = 10
DEFAULT_MAX_DAILY = 6
DEFAULT_PRIORITY = 2


def _norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def _to_int(value, default: int) -> int:
    text = _norm(value)
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _locate_header(rows: list[list]) -> tuple[int, dict[str, int]]:
    """어떤 행이 헤더인지 별칭 표로 찾아낸다."""
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        labels = {_norm(v): c for c, v in enumerate(row) if _norm(v)}
        mapping = {}
        for field, aliases in COLUMN_ALIASES.items():
            for alias in aliases:
                if alias in labels:
                    mapping[field] = labels[alias]
                    break
        if all(field in mapping for field in REQUIRED):
            return index, mapping
    raise ValidationFailed(
        "면접관명단 헤더를 찾지 못했습니다 — 사번 · 소속팀 컬럼이 필요합니다"
    )


def parse_roster(data: bytes) -> list[dict]:
    """엑셀 바이트 → 면접관 dict 목록 (사번 기준 중복 제거)."""
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValidationFailed(f"엑셀을 열 수 없습니다: {exc}") from exc
    try:
        sheet = workbook[workbook.sheetnames[0]]
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()

    header_row, columns = _locate_header(rows)

    def cell(row: list, field: str):
        index = columns.get(field)
        return row[index] if index is not None and index < len(row) else None

    out: list[dict] = []
    seen: set[str] = set()
    for row in rows[header_row + 1:]:
        interviewer_id = _norm(cell(row, "interviewer_id"))
        team = _norm(cell(row, "team"))
        if not interviewer_id or interviewer_id in seen:
            continue
        if interviewer_id in [a for aliases in COLUMN_ALIASES.values() for a in aliases]:
            continue  # 반복된 헤더 행
        if not team:
            continue
        seen.add(interviewer_id)
        out.append({
            "interviewer_id": interviewer_id,
            "name": _norm(cell(row, "name")),
            "team": team,
            "email": _norm(cell(row, "email")),
            "max_daily": _to_int(cell(row, "max_daily"), DEFAULT_MAX_DAILY),
            "priority": _to_int(cell(row, "priority"), DEFAULT_PRIORITY),
        })
    if not out:
        raise ValidationFailed("면접관 행을 하나도 읽지 못했습니다")
    return out


def import_roster(db: Session, data: bytes) -> dict:
    """업로드한 명단을 마스터에 반영한다 (사번 기준 upsert).

    가용성 컬럼은 건드리지 않는다 — 이미 회신으로 채워진 값을 덮어쓰면 안 된다.
    """
    parsed = parse_roster(data)
    created = updated = 0
    for row in parsed:
        existing = db.get(Interviewer, row["interviewer_id"])
        if existing is None:
            db.add(Interviewer(**row, availability={}))
            created += 1
        else:
            for field, value in row.items():
                if field != "interviewer_id":
                    setattr(existing, field, value)
            updated += 1
    db.commit()
    teams = sorted({row["team"] for row in parsed})
    return {
        "parsed": len(parsed),
        "created": created,
        "updated": updated,
        "teams": teams,
        "interviewers": parsed,
    }


def select_for_round(db: Session, round_id: str, interviewer_ids: list[str],
                     actor: str = "console") -> dict:
    """회차 선별 목록을 통째로 교체한다."""
    known = set(db.scalars(
        select(Interviewer.interviewer_id).where(
            Interviewer.interviewer_id.in_(interviewer_ids)
        )
    ).all()) if interviewer_ids else set()
    unknown = [i for i in interviewer_ids if i not in known]
    if unknown:
        raise ValidationFailed(
            f"등록되지 않은 면접관입니다: {', '.join(unknown[:5])}",
            {"unknown": unknown},
        )

    db.execute(delete(RoundInterviewer).where(RoundInterviewer.round_id == round_id))
    for interviewer_id in dict.fromkeys(interviewer_ids):
        db.add(RoundInterviewer(
            round_id=round_id, interviewer_id=interviewer_id, selected_by=actor
        ))
    db.commit()
    return {"round_id": round_id, "selected": len(set(interviewer_ids))}


def selected_ids(db: Session, round_id: str) -> list[str]:
    return list(db.scalars(
        select(RoundInterviewer.interviewer_id)
        .where(RoundInterviewer.round_id == round_id)
        .order_by(RoundInterviewer.interviewer_id)
    ).all())


def list_for_round(db: Session, round_id: str) -> list[dict]:
    ids = selected_ids(db, round_id)
    if not ids:
        return []
    rows = db.scalars(
        select(Interviewer).where(Interviewer.interviewer_id.in_(ids))
        .order_by(Interviewer.team, Interviewer.priority, Interviewer.interviewer_id)
    ).all()
    return [
        {
            "interviewer_id": r.interviewer_id,
            "name": r.name,
            "team": r.team,
            "email": r.email,
            "max_daily": r.max_daily,
            "priority": r.priority,
        }
        for r in rows
    ]
