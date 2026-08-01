"""GET /api/v1/rounds/{round_id}/availability — 회차별 면접관 가용성 집계

04(스케줄러)가 시간표를 짤 때 쓰는 입력이다. 응답 payload 는
`available_slots: [{day, hour}]` 형태라 04가 기대하는 `{날: [시간대]}` 로
뒤집어서 내려준다. 회신하지 않은 면접위원은 가용 슬롯을 알 수 없으므로
기본적으로 제외한다(include_pending=true 로 켤 수 있다).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.domain.invitee import Invitee
from app.domain.reminder import Reminder
from app.domain.request import Request
from app.domain.response import Response
from app.infrastructure.db import get_db
from app.schemas import ok

router = APIRouter(prefix="/api/v1/rounds", tags=["rounds"])

DEFAULT_MAX_DAILY = 6
LEADER_PRIORITY = 1
MEMBER_PRIORITY = 2


def _availability(payload: dict | None) -> dict[str, list[str]]:
    """[{day, hour}] → {day: [hour, ...]} (날별 등장 순서 유지)"""
    out: dict[str, list[str]] = {}
    for slot in (payload or {}).get("available_slots", []) or []:
        day, hour = slot.get("day"), slot.get("hour")
        if not day or not hour:
            continue
        hours = out.setdefault(day, [])
        if hour not in hours:
            hours.append(hour)
    return out


def _priority(invitee: Invitee) -> int:
    """부서장 본인이면 우선 배정 대상으로 본다.

    03에는 직급 컬럼이 없고 dept_leader_email 만 있다. 자기 자신이 부서장으로
    적힌 사람은 리더라는 뜻이라 04의 priority=1 에 대응시킨다.
    """
    leader = (invitee.dept_leader_email or "").strip().lower()
    return LEADER_PRIORITY if leader and leader == (invitee.email or "").strip().lower() else MEMBER_PRIORITY


@router.get("/{round_id}/availability", summary="회차 면접관 가용성 (04 입력)")
def get_availability(
    round_id: str,
    team: str | None = Query(default=None, description="팀 이름으로 필터"),
    include_pending: bool = Query(default=False, description="미회신자도 포함(가용 슬롯 없음)"),
    db: Session = Depends(get_db),
):
    query = (
        db.query(Invitee)
        .join(Request, Invitee.request_id == Request.request_id)
        .filter(Request.round_id == round_id)
    )
    if team:
        query = query.filter(Invitee.team == team)

    rows = []
    pending = 0
    for invitee in query.all():
        payload = invitee.response.payload if invitee.has_responded else None
        availability = _availability(payload)
        if not availability:
            pending += 1
            if not include_pending:
                continue
        rows.append({
            "interviewer_id": invitee.invitee_id,
            "name": invitee.name,
            "team": invitee.team,
            "org": invitee.org,
            "email": invitee.email,
            "max_daily": int((payload or {}).get("max_daily") or DEFAULT_MAX_DAILY),
            "priority": _priority(invitee),
            "job_role": (payload or {}).get("job_role"),
            "availability": availability,
            "slot_count": sum(len(h) for h in availability.values()),
            "responded": invitee.has_responded,
        })

    rows.sort(key=lambda r: (r["team"], r["priority"], r["name"]))
    return ok(rows)


@router.get("/{round_id}/availability/summary", summary="가용성 집계 요약")
def get_availability_summary(round_id: str, db: Session = Depends(get_db)):
    """팀별로 회신 인원과 총 가용 슬롯을 센다 (콘솔 4번 메뉴 표시용)."""
    query = (
        db.query(Invitee)
        .join(Request, Invitee.request_id == Request.request_id)
        .filter(Request.round_id == round_id)
    )
    teams: dict[str, dict] = {}
    for invitee in query.all():
        bucket = teams.setdefault(
            invitee.team, {"team": invitee.team, "invited": 0, "responded": 0, "slots": 0}
        )
        bucket["invited"] += 1
        if invitee.has_responded:
            bucket["responded"] += 1
            availability = _availability(invitee.response.payload)
            bucket["slots"] += sum(len(h) for h in availability.values())

    rows = sorted(teams.values(), key=lambda r: r["team"])
    return ok({
        "round_id": round_id,
        "teams": rows,
        "invited": sum(r["invited"] for r in rows),
        "responded": sum(r["responded"] for r in rows),
        "total_slots": sum(r["slots"] for r in rows),
    })


@router.delete("/{round_id}", summary="회차 회신 이력 비우기")
def reset_round(round_id: str, db: Session = Depends(get_db)):
    """1단계에서 명단을 다시 올릴 때 이 회차의 요청 · 회신을 함께 지운다.

    지원자 명단이 바뀌면 그 명단으로 만든 뒤 단계는 전부 무효다. 남겨 두면
    화면마다 다른 회차를 보고 있게 된다.
    """
    request_ids = [
        r.request_id for r in db.query(Request).filter(Request.round_id == round_id).all()
    ]
    if not request_ids:
        return ok({"round_id": round_id, "deleted_requests": 0,
                   "deleted_invitees": 0, "deleted_responses": 0})

    invitee_ids = [
        i.invitee_id for i in db.query(Invitee)
        .filter(Invitee.request_id.in_(request_ids)).all()
    ]
    responses = reminders = 0
    if invitee_ids:
        responses = (
            db.query(Response).filter(Response.invitee_id.in_(invitee_ids))
            .delete(synchronize_session=False)
        )
        reminders = (
            db.query(Reminder).filter(Reminder.invitee_id.in_(invitee_ids))
            .delete(synchronize_session=False)
        )
    invitees = (
        db.query(Invitee).filter(Invitee.request_id.in_(request_ids))
        .delete(synchronize_session=False)
    )
    requests = (
        db.query(Request).filter(Request.round_id == round_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return ok({
        "round_id": round_id,
        "deleted_requests": requests,
        "deleted_invitees": invitees,
        "deleted_responses": responses,
        "deleted_reminders": reminders,
    })
