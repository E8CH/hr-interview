"""재편성 오케스트레이션 — API 와 도메인 로직을 잇는다."""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import events
from app.domain.repair_plan import RepairChange, RepairPlan, SlotRef
from app.domain.schedule import ScheduleAssignment, ScheduleSnapshot
from app.infrastructure.db import (LockMapRow, RepairEventRow, RepairPlanRow,
                                   SelectedPlanRow, ScheduleSnapshotRow, SessionLocal)
from app.services import lock_service, plan_generator, scheduler_client
from app.services.constraint_recheck import check_hard_constraints

log = logging.getLogger("repair-engine.service")


class RepairError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------
# 스냅샷
# --------------------------------------------------------------------------
def load_snapshot(session: Session, schedule_id: str, round_id: str,
                  lock_level: str = "CONFIRMED") -> ScheduleSnapshot:
    """로컬 스냅샷이 있으면 재사용, 없으면 Service 04 에서 로드한다."""
    row = session.get(ScheduleSnapshotRow, schedule_id)
    if row is not None:
        return ScheduleSnapshot.model_validate(row.payload)

    snapshot = scheduler_client.fetch_schedule(schedule_id, round_id, lock_level)
    session.add(ScheduleSnapshotRow(
        schedule_id=schedule_id, round_id=round_id,
        payload=snapshot.model_dump(mode="json"), updated_at=datetime.utcnow()))
    session.commit()
    lock_service.sync_from_snapshot(session, snapshot)
    return snapshot


def save_snapshot(session: Session, snapshot: ScheduleSnapshot) -> None:
    row = session.get(ScheduleSnapshotRow, snapshot.schedule_id)
    payload = snapshot.model_dump(mode="json")
    if row is None:
        session.add(ScheduleSnapshotRow(schedule_id=snapshot.schedule_id,
                                        round_id=snapshot.round_id,
                                        payload=payload,
                                        updated_at=datetime.utcnow()))
    else:
        row.payload = payload
        row.updated_at = datetime.utcnow()
    session.commit()


def prefetch_schedule(schedule_id: str, round_id: str,
                      lock_level: str = "CONFIRMED") -> None:
    """SCHEDULE_LOCKED 구독 핸들러용 — 자체 세션으로 스냅샷을 적재한다."""
    session = SessionLocal()
    try:
        load_snapshot(session, schedule_id, round_id, lock_level)
    finally:
        session.close()


# --------------------------------------------------------------------------
# 이벤트 접수 + Plan 생성
# --------------------------------------------------------------------------
def _persist_plans(session: Session, plans: list[RepairPlan]) -> None:
    for plan in plans:
        session.add(RepairPlanRow(
            plan_id=plan.plan_id, event_id=plan.event_id, plan_type=plan.plan_type,
            rebooked_count=plan.rebooked_count, deferred_count=plan.deferred_count,
            hard_violations=plan.hard_violations, soft_penalty=plan.soft_penalty,
            plan_detail=plan.to_detail(), generated_at=plan.generated_at))
    session.commit()


def _create_event(session: Session, *, round_id: str, schedule_id: str,
                  trigger_type: str, trigger_target: str, reported_by: str,
                  affected: list[str], correlation_id: str) -> RepairEventRow:
    row = RepairEventRow(
        event_id=str(uuid4()), round_id=round_id, schedule_id=schedule_id,
        trigger_type=trigger_type, trigger_target=trigger_target,
        reported_at=datetime.utcnow(), reported_by=reported_by,
        status="pending", correlation_id=correlation_id,
        affected={"applicant_ids": affected})
    session.add(row)
    session.commit()
    return row


def _generate_for(session: Session, event_row: RepairEventRow,
                  snapshot: ScheduleSnapshot, affected: list[str],
                  excluded_interviewers: set[str] | None = None) -> list[RepairPlan]:
    lock_overrides = lock_service.get_locks(session, snapshot.schedule_id)
    plans = plan_generator.generate_plans(
        event_row.event_id, snapshot, affected, lock_overrides, excluded_interviewers)
    _persist_plans(session, plans)
    return plans


def report_noshow(session: Session, *, round_id: str, schedule_id: str,
                  noshow_applicant_ids: list[str], reported_by: str) -> dict:
    if not noshow_applicant_ids:
        raise RepairError("VALIDATION_FAILED", "noshow_applicant_ids 가 비어 있습니다", 422)

    snapshot = load_snapshot(session, schedule_id, round_id)
    known = {a.applicant_id for a in snapshot.assignments}
    unknown = [i for i in noshow_applicant_ids if i not in known]
    if unknown:
        raise RepairError("NOT_FOUND",
                          f"시간표에 없는 지원자: {unknown}", 404)

    correlation_id = str(uuid4())
    event_row = _create_event(
        session, round_id=round_id, schedule_id=schedule_id,
        trigger_type="noshow", trigger_target=",".join(noshow_applicant_ids)[:64],
        reported_by=reported_by, affected=list(noshow_applicant_ids),
        correlation_id=correlation_id)

    events.publish_noshow_reported(round_id, correlation_id,
                                   list(noshow_applicant_ids), reported_by)
    plans = _generate_for(session, event_row, snapshot, list(noshow_applicant_ids))
    log.info("noshow 접수 event_id=%s 대상=%d명 plans=%d",
             event_row.event_id, len(noshow_applicant_ids), len(plans))
    return {"event_id": event_row.event_id, "status": event_row.status,
            "plan_count": len(plans)}


def report_cancel(session: Session, *, round_id: str, schedule_id: str,
                  cancel_type: str, target_id: str, reason: str = "") -> dict:
    if cancel_type not in ("applicant", "interviewer"):
        raise RepairError("VALIDATION_FAILED",
                          "cancel_type 은 'applicant' 또는 'interviewer'", 422)

    snapshot = load_snapshot(session, schedule_id, round_id)
    excluded: set[str] = set()

    if cancel_type == "applicant":
        affected = [a.applicant_id for a in snapshot.assignments
                    if a.applicant_id == target_id]
        trigger_type = "cancel_applicant"
    else:
        affected = [a.applicant_id for a in snapshot.assignments
                    if a.interviewer_id == target_id]
        excluded = {target_id}
        trigger_type = "cancel_interviewer"

    if not affected:
        raise RepairError("NOT_FOUND", f"대상 배정을 찾을 수 없습니다: {target_id}", 404)

    correlation_id = str(uuid4())
    event_row = _create_event(
        session, round_id=round_id, schedule_id=schedule_id,
        trigger_type=trigger_type, trigger_target=target_id,
        reported_by=reason or "system", affected=affected,
        correlation_id=correlation_id)

    plans = _generate_for(session, event_row, snapshot, affected, excluded)
    return {"event_id": event_row.event_id, "status": event_row.status,
            "affected_count": len(affected), "plan_count": len(plans)}


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------
def _plan_rows(session: Session, event_id: str) -> list[RepairPlanRow]:
    order = {"A_safe": 0, "B_defer": 1, "C_cross_team": 2}
    rows = list(session.scalars(
        select(RepairPlanRow).where(RepairPlanRow.event_id == event_id)))
    return sorted(rows, key=lambda r: order.get(r.plan_type, 9))


def get_event(session: Session, event_id: str) -> RepairEventRow:
    row = session.get(RepairEventRow, event_id)
    if row is None:
        raise RepairError("NOT_FOUND", f"event_id 없음: {event_id}", 404)
    return row


def get_plans(session: Session, event_id: str) -> dict:
    event_row = get_event(session, event_id)
    rows = _plan_rows(session, event_id)
    plans = []
    for r in rows:
        detail = r.plan_detail or {}
        summary = {
            "plan_id": r.plan_id, "type": r.plan_type,
            "rebooked": r.rebooked_count, "deferred": r.deferred_count,
            "hard": r.hard_violations, "soft": r.soft_penalty,
            "description": detail.get("description", ""),
        }
        if r.plan_type == "C_cross_team":
            summary["cross_team_count"] = detail.get("cross_team_count", 0)
        if detail.get("warning"):
            summary["warning"] = detail["warning"]
        summary["changes"] = detail.get("changes", [])
        plans.append(summary)
    return {"event_id": event_id, "status": event_row.status, "plans": plans}


# --------------------------------------------------------------------------
# Plan 선택 · 적용
# --------------------------------------------------------------------------
def _apply_changes(snapshot: ScheduleSnapshot,
                   changes: list[RepairChange]) -> tuple[ScheduleSnapshot, list[str]]:
    by_applicant = {c.applicant_id: c for c in changes}
    remaining = [a for a in snapshot.assignments if a.applicant_id not in by_applicant]

    deferred_ids: list[str] = []
    consumed: set[tuple[str, str, str]] = set()
    for change in changes:
        if change.action == "defer":
            deferred_ids.append(change.applicant_id)
            continue
        slot = change.to_slot
        if slot is None:
            deferred_ids.append(change.applicant_id)
            continue
        remaining.append(ScheduleAssignment(
            assignment_id=f"RP-{change.applicant_id}-{slot.day}{slot.hour}",
            applicant_id=change.applicant_id, interviewer_id=slot.interviewer_id,
            day=slot.day, hour=slot.hour, team=slot.team,
            lock_level=change.lock_level, reason_tags=["HR_MANUAL"]))
        consumed.add(slot.key)

    snapshot.assignments = remaining
    snapshot.reserved_slots = [s for s in snapshot.reserved_slots
                               if s.key not in consumed]
    return snapshot, deferred_ids


def select_plan(session: Session, event_id: str, plan_id: str,
                selected_by: str) -> dict:
    event_row = get_event(session, event_id)

    already = session.scalars(
        select(SelectedPlanRow).where(SelectedPlanRow.event_id == event_id)).first()
    if already is not None and already.applied_at is not None:
        raise RepairError("PLAN_ALREADY_APPLIED",
                          f"이미 적용된 이벤트입니다 (plan_id={already.plan_id})", 409)

    plan_row = session.get(RepairPlanRow, plan_id)
    if plan_row is None or plan_row.event_id != event_id:
        raise RepairError("NOT_FOUND", f"plan_id 없음: {plan_id}", 404)

    detail = plan_row.plan_detail or {}
    changes = [RepairChange.model_validate(c) for c in detail.get("changes", [])]
    reopened = [SlotRef.model_validate(s) for s in detail.get("reopened_slots", [])]

    snapshot = load_snapshot(session, event_row.schedule_id, event_row.round_id)
    locked_before = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
                     for a in snapshot.assignments if a.lock_level == "LOCKED"}

    snapshot, deferred_ids = _apply_changes(snapshot, changes)

    # 적용 후에도 하드 위반 0 이어야 한다 — 아니면 커밋하지 않는다.
    violations = check_hard_constraints(snapshot.assignments, snapshot.interviewers)
    if violations:
        raise RepairError("INTEGRITY_VIOLATION",
                          f"적용 시 하드 위반 {len(violations)}건 발생 — 롤백", 409)

    # LOCKED 배정은 이동되지 않았음을 재확인 (이월된 노쇼자는 제외)
    after = {a.applicant_id: (a.day, a.hour, a.interviewer_id)
             for a in snapshot.assignments}
    for applicant_id, slot in locked_before.items():
        if applicant_id in deferred_ids:
            continue
        if after.get(applicant_id) != slot:
            raise RepairError("INTEGRITY_VIOLATION",
                              f"LOCKED 배정이 이동되었습니다: {applicant_id}", 409)

    save_snapshot(session, snapshot)

    rebooked = sum(1 for c in changes if c.action == "rebook")
    now = datetime.utcnow()
    selection = SelectedPlanRow(
        selection_id=str(uuid4()), event_id=event_id, plan_id=plan_id,
        selected_by=selected_by, selected_at=now, applied_at=now,
        affected_count=len(changes))
    session.add(selection)
    event_row.status = "resolved" if rebooked else "deferred"
    session.commit()

    # --- 이벤트 발행 ---
    events.publish_repair_executed(
        event_row.round_id, event_row.correlation_id, event_id,
        plan_row.plan_type, rebooked, len(deferred_ids),
        event_row.schedule_id, selected_by)
    if deferred_ids:
        events.publish_participant_deferred(
            event_row.round_id, event_row.correlation_id, event_id,
            deferred_ids, f"{plan_row.plan_type} 적용")
    reopened_payload = [s.model_dump(mode="json") for s in reopened]
    if reopened_payload:
        events.publish_slot_reopened(
            event_row.round_id, event_row.correlation_id, event_id, reopened_payload)

    return {"applied": True, "plan_type": plan_row.plan_type,
            "affected_assignments": len(changes),
            "rebooked": rebooked, "deferred": len(deferred_ids),
            "hard_violations": 0,
            "reopened_slots": len(reopened_payload)}


# --------------------------------------------------------------------------
# 회차 비우기
# --------------------------------------------------------------------------
def reset_round(session: Session, round_id: str) -> dict:
    """그 회차의 재편성 기록과 시간표 사본을 지운다.

    1단계에서 명단을 다시 받으면 시간표(04)가 사라진다. 여기 남은 사본과
    이벤트는 없어진 시간표를 가리키는 껍데기라, 두면 다음 회차 작업에서
    옛 자리 배치가 되살아난다.
    """
    event_ids = list(session.scalars(
        select(RepairEventRow.event_id).where(RepairEventRow.round_id == round_id)))
    schedule_ids = list(session.scalars(
        select(ScheduleSnapshotRow.schedule_id)
        .where(ScheduleSnapshotRow.round_id == round_id)))
    schedule_ids += list(session.scalars(
        select(RepairEventRow.schedule_id).where(RepairEventRow.round_id == round_id)))

    selections = plans = events_deleted = locks = 0
    if event_ids:
        selections = session.query(SelectedPlanRow).filter(
            SelectedPlanRow.event_id.in_(event_ids)).delete(synchronize_session=False)
        plans = session.query(RepairPlanRow).filter(
            RepairPlanRow.event_id.in_(event_ids)).delete(synchronize_session=False)
        events_deleted = session.query(RepairEventRow).filter(
            RepairEventRow.round_id == round_id).delete(synchronize_session=False)
    if schedule_ids:
        locks = session.query(LockMapRow).filter(
            LockMapRow.schedule_id.in_(set(schedule_ids))).delete(synchronize_session=False)
    snapshots = session.query(ScheduleSnapshotRow).filter(
        ScheduleSnapshotRow.round_id == round_id).delete(synchronize_session=False)
    session.commit()
    return {"round_id": round_id, "deleted_events": events_deleted,
            "deleted_plans": plans, "deleted_selections": selections,
            "deleted_snapshots": snapshots, "deleted_locks": locks}


# --------------------------------------------------------------------------
# 감사 로그
# --------------------------------------------------------------------------
def audit_log(session: Session, round_id: str) -> list[dict]:
    event_rows = list(session.scalars(
        select(RepairEventRow).where(RepairEventRow.round_id == round_id)))
    event_rows.sort(key=lambda r: r.reported_at or datetime.min)

    selections = {
        row.event_id: row
        for row in session.scalars(select(SelectedPlanRow))
    }

    entries: list[dict] = []
    for ev in event_rows:
        sel = selections.get(ev.event_id)
        plan_row = session.get(RepairPlanRow, sel.plan_id) if sel else None
        entries.append({
            "event_id": ev.event_id,
            "round_id": ev.round_id,
            "schedule_id": ev.schedule_id,
            "trigger_type": ev.trigger_type,
            "trigger_target": ev.trigger_target,
            "reported_by": ev.reported_by,
            "reported_at": ev.reported_at.isoformat() if ev.reported_at else None,
            "status": ev.status,
            "affected_applicant_ids": (ev.affected or {}).get("applicant_ids", []),
            "selected_plan": plan_row.plan_type if plan_row else None,
            "selected_by": sel.selected_by if sel else None,
            "applied_at": sel.applied_at.isoformat() if sel and sel.applied_at else None,
            "affected_count": sel.affected_count if sel else 0,
            "plan_count": len(_plan_rows(session, ev.event_id)),
        })
    return entries
