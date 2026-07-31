"""배포안 서비스 계층 — 엔진 실행 · 영속화 · 이벤트 발행."""
from __future__ import annotations

import time
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.plan import (
    MODE_AUTO,
    MODE_INHERIT,
    Assignment,
    Move,
    PlanMode,
    PlanSummary,
)
from app.events import publish_adjusted, publish_approved, publish_plan_created, is_halted
from app.infrastructure.db import (
    AssignmentReasonORM,
    DistributionPlanORM,
    load_profiles,
    utcnow,
)
from app.infrastructure.version_client import MasterNotFound, VersionClient
from app.services.distributor_engine import distribute, inherit


class ServiceError(Exception):
    """API 에러 코드가 붙은 도메인 예외."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _plan_summary(plan: DistributionPlanORM, team_counts: dict[str, int]) -> PlanSummary:
    return PlanSummary(
        plan_id=plan.plan_id,
        round_id=plan.round_id,
        status=plan.status,
        # mode 가 생기기 전 배정안은 NULL — 그때는 전부 재배치였다
        mode=plan.mode or MODE_AUTO,
        master_version_id=plan.master_version_id,
        total_applicants=plan.total_applicants,
        team_counts=team_counts,
        duplicate_count=plan.duplicate_count,
        created_by=plan.created_by,
        created_at=plan.created_at,
        approved_at=plan.approved_at,
        approved_by=plan.approved_by,
    )


def _team_counts(session: Session, plan_id: str) -> dict[str, int]:
    rows = session.scalars(
        select(AssignmentReasonORM).where(
            AssignmentReasonORM.plan_id == plan_id,
            AssignmentReasonORM.is_duplicate.is_(False),
        )
    ).all()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.team_name] = counts.get(row.team_name, 0) + 1
    return dict(sorted(counts.items()))


def get_plan(session: Session, plan_id: str) -> DistributionPlanORM:
    plan = session.get(DistributionPlanORM, plan_id)
    if plan is None:
        raise ServiceError("NOT_FOUND", f"plan_id={plan_id} 없음", status_code=404)
    return plan


def list_plans_for_round(session: Session, round_id: str) -> list[dict]:
    """그 회차에서 만든 배포안을 최신순으로 — 화면이 배정안 번호를 잊었을 때 되찾는 길.

    콘솔은 배정안 번호를 브라우저 세션에만 들고 있어서, 새로 열거나 다른 사람이
    이어받으면 '2단계에서 팀별 명단을 먼저 나눠 주세요' 만 보게 된다. 회차만 알면
    여기서 다시 찾아 쓸 수 있게 한다.
    """
    rows = session.scalars(
        select(DistributionPlanORM)
        .where(DistributionPlanORM.round_id == round_id)
        .order_by(DistributionPlanORM.created_at.desc())
    ).all()
    return [
        {
            "plan_id": row.plan_id,
            "round_id": row.round_id,
            "status": row.status,
            "mode": row.mode or MODE_AUTO,
            "total_applicants": row.total_applicants,
            "duplicate_count": row.duplicate_count,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def create_plan(
    session: Session,
    round_id: str,
    master_version_id: str,
    mode: PlanMode = MODE_AUTO,
    allow_duplicate: bool = True,
    duplicate_score_threshold: float = 0.8,
    created_by: str | None = None,
    client: VersionClient | None = None,
) -> PlanSummary:
    """배포 파이프라인 실행 후 배포안을 저장하고 이벤트를 발행한다.

    `mode="inherit"` 이면 1단계에서 팀이 적어 낸 '담당팀' 을 그대로 옮기고,
    `mode="auto"` 면 점수로 5팀에 새로 섞는다(명단 재배치). 중복 허용·기준 점수는
    재배치에서만 쓰인다 — 승계에서는 팀이 적어 낸 만큼이 그대로 중복이다.
    """
    if is_halted(round_id):
        raise ServiceError(
            "INTEGRITY_VIOLATION",
            f"회차 {round_id} 는 무결성 위반으로 배포가 중단된 상태입니다",
            status_code=409,
        )

    profiles = load_profiles(session)
    if not profiles:
        raise ServiceError("VALIDATION_FAILED", "팀 프로필이 비어 있습니다", status_code=400)

    started = time.perf_counter()
    try:
        applicants = (client or VersionClient()).fetch_master(master_version_id)
    except MasterNotFound as exc:
        raise ServiceError("NOT_FOUND", str(exc), status_code=404) from exc

    if mode == MODE_INHERIT:
        if not any(a.assigned_teams for a in applicants):
            raise ServiceError(
                "VALIDATION_FAILED",
                "취합파일에 '담당팀' 이 비어 있습니다 — 1단계에서 팀별 명단을 함께 "
                "올려 다시 합치거나, [명단 재배치] 로 새로 나눠 주세요",
                status_code=400,
            )
        result = inherit(applicants, profiles)
    else:
        result = distribute(
            applicants,
            profiles,
            allow_duplicate=allow_duplicate,
            duplicate_score_threshold=duplicate_score_threshold,
        )
    elapsed = time.perf_counter() - started

    plan = DistributionPlanORM(
        plan_id=str(uuid4()),
        round_id=round_id,
        status="draft",
        mode=mode,
        master_version_id=master_version_id,
        total_applicants=result.total_applicants,
        duplicate_count=result.duplicate_count,
        created_by=created_by,
    )
    session.add(plan)

    by_id = {a.applicant_id: a for a in applicants}
    for assignment in result.assignments:
        source = by_id.get(assignment.applicant_id)
        session.add(
            AssignmentReasonORM(
                id=str(uuid4()),
                plan_id=plan.plan_id,
                applicant_id=assignment.applicant_id,
                applicant_name=source.name if source else None,
                team_name=assignment.team_name,
                score=assignment.score,
                tags=assignment.tags,
                is_duplicate=assignment.is_duplicate,
                primary_team=assignment.primary_team,
                snapshot=source.model_dump(mode="json") if source else {},
            )
        )
    session.commit()

    summary = _plan_summary(plan, result.team_counts)
    summary.elapsed_seconds = round(elapsed, 3)
    summary.unassigned = result.unassigned
    summary.filtered_count = result.filtered_count
    summary.unknown_teams = result.unknown_teams

    publish_plan_created(
        round_id=round_id,
        plan_id=plan.plan_id,
        team_counts=result.team_counts,
        total_applicants=result.total_applicants,
    )
    return summary


def get_plan_summary(session: Session, plan_id: str) -> PlanSummary:
    plan = get_plan(session, plan_id)
    return _plan_summary(plan, _team_counts(session, plan_id))


def get_plan_detail(session: Session, plan_id: str) -> dict:
    """GET /api/v1/distribute/{plan_id} 응답용 상세 구조."""
    plan = get_plan(session, plan_id)
    rows = session.scalars(
        select(AssignmentReasonORM)
        .where(AssignmentReasonORM.plan_id == plan_id)
        .order_by(AssignmentReasonORM.team_name, AssignmentReasonORM.score.desc())
    ).all()
    teams: dict[str, list[dict]] = {}
    for row in rows:
        teams.setdefault(row.team_name, []).append(
            {
                "applicant_id": row.applicant_id,
                "name": row.applicant_name,
                "score": row.score,
                "tags": list(row.tags or []),
                "is_duplicate": row.is_duplicate,
                "primary_team": row.primary_team,
            }
        )
    return {
        "plan_id": plan.plan_id,
        "round_id": plan.round_id,
        "status": plan.status,
        # 이 명단을 무엇으로 만들었는지 — 화면이 출처를 밝힌다
        "mode": plan.mode or MODE_AUTO,
        "master_version_id": plan.master_version_id,
        "total_applicants": plan.total_applicants,
        "duplicate_count": plan.duplicate_count,
        "team_counts": _team_counts(session, plan_id),
        "teams": teams,
        # 누가 언제 만들고 확정했는지 — 화면이 이 값으로 확정 여부를 알린다
        "created_by": plan.created_by,
        "created_at": plan.created_at,
        "approved_by": plan.approved_by,
        "approved_at": plan.approved_at,
        "reject_reason": plan.reject_reason,
    }


def get_plan_applicants(session: Session, plan_id: str) -> list[dict]:
    """확정 명단 — 04(스케줄러)가 이걸 그대로 배정에 쓴다.

    두 팀이 같이 보기로 한 사람은 자리가 둘이므로 **행도 둘** 나간다. 예전에는
    여기서 중복 자리를 빼고 내려줬는데, 그러면 팀에 나가는 엑셀(export)과 부서가
    받는 명단에는 그 사람이 있는데 시간표에는 한 팀에만 잡혀서, 나머지 한 팀은
    면접 볼 사람이 시간표에 없었다. 자리가 둘이면 면접도 두 번이다.
    `is_duplicate`/`primary_team` 으로 어느 쪽이 주 팀인지 구분한다.

    snapshot 에 마스터 원본 행이 통째로 들어 있으므로 학위·학점·타겟랩까지
    같이 내려준다. 04가 규칙1(학위 균형) 계산에 degree_type 이 필요하다.
    """
    get_plan(session, plan_id)  # 없는 plan_id 면 404
    rows = session.scalars(
        select(AssignmentReasonORM)
        .where(AssignmentReasonORM.plan_id == plan_id)
        .order_by(
            AssignmentReasonORM.team_name,
            # 주 팀 자리를 먼저 — 같은 사람의 두 행 중 어느 쪽이 기준인지 고정된다
            AssignmentReasonORM.is_duplicate,
            AssignmentReasonORM.score.desc(),
        )
    ).all()
    out: list[dict] = []
    for row in rows:
        snapshot = row.snapshot or {}
        out.append(
            {
                "applicant_id": row.applicant_id,
                "name": row.applicant_name or snapshot.get("name") or "",
                "team": row.team_name,
                "team_1st": snapshot.get("team_1st"),
                "degree_type": snapshot.get("degree_type") or "",
                "major_final": snapshot.get("major_final"),
                "gpa_final": snapshot.get("gpa_final"),
                "target_lab": snapshot.get("target_lab"),
                "score": row.score,
                "reason_tags": list(row.tags or []),
                # 같이 보는 자리인지 · 그때 주 팀은 어디인지
                "is_duplicate": row.is_duplicate,
                "primary_team": row.primary_team,
            }
        )
    return out


def approve_plan(session: Session, plan_id: str, actor: str) -> PlanSummary:
    plan = get_plan(session, plan_id)
    if plan.status not in ("draft", "adjusted"):
        raise ServiceError(
            "VALIDATION_FAILED",
            f"status={plan.status} 상태에서는 승인할 수 없습니다",
            status_code=409,
        )
    plan.status = "approved"
    plan.approved_at = utcnow()
    plan.approved_by = actor
    session.commit()

    publish_approved(
        round_id=plan.round_id,
        plan_id=plan.plan_id,
        approver=actor,
        total_applicants=plan.total_applicants,
    )
    return _plan_summary(plan, _team_counts(session, plan_id))


def reject_plan(session: Session, plan_id: str, reason: str) -> PlanSummary:
    plan = get_plan(session, plan_id)
    if plan.status == "approved":
        raise ServiceError(
            "VALIDATION_FAILED", "이미 승인된 배포안은 반려할 수 없습니다", status_code=409
        )
    plan.status = "rejected"
    plan.reject_reason = reason
    session.commit()
    return _plan_summary(plan, _team_counts(session, plan_id))


def reset_round(session: Session, round_id: str) -> dict:
    """그 회차의 배포안을 전부 지운다.

    1단계에서 지원자 명단을 다시 올리면 그 명단으로 나눈 팀은 무효다. 남겨 두면
    2단계가 옛 배포안을 되찾아 와(`round_plan_id`) 지운 명단의 팀 나눔으로
    계속 진행하게 된다.
    """
    plan_ids = list(
        session.scalars(
            select(DistributionPlanORM.plan_id).where(
                DistributionPlanORM.round_id == round_id
            )
        )
    )
    if not plan_ids:
        return {"round_id": round_id, "deleted_plans": 0, "deleted_assignments": 0}

    assignments = (
        session.query(AssignmentReasonORM)
        .filter(AssignmentReasonORM.plan_id.in_(plan_ids))
        .delete(synchronize_session=False)
    )
    plans = (
        session.query(DistributionPlanORM)
        .filter(DistributionPlanORM.round_id == round_id)
        .delete(synchronize_session=False)
    )
    session.commit()
    return {"round_id": round_id, "deleted_plans": plans,
            "deleted_assignments": assignments}


def adjust_plan(
    session: Session, plan_id: str, moves: list[Move], actor: str | None = None
) -> PlanSummary:
    """HR 수동 조정 — 배정 팀 변경 후 HR_MANUAL 태그 부착.

    두 팀이 같이 보는 사람은 행이 둘이다. `from` 으로 지목한 그 행 하나만 옮기므로
    나머지 자리는 그대로 남는다 — 같이 보기를 풀려는 게 아니라 한쪽 팀만 바꾸는
    조정이다. 이미 그 사람이 있는 팀으로는 옮기지 않는다(한 팀이 같은 사람을 두 번
    보게 된다). 주 팀 자리를 옮기면 같이 보는 자리의 `primary_team` 도 따라간다.
    """
    plan = get_plan(session, plan_id)
    if plan.status == "rejected":
        raise ServiceError("VALIDATION_FAILED", "반려된 배포안은 조정할 수 없습니다", status_code=409)
    if not moves:
        raise ServiceError("VALIDATION_FAILED", "moves가 비어 있습니다", status_code=400)

    valid_teams = {p.team_name for p in load_profiles(session)}
    applied: list[dict] = []

    for move in moves:
        if move.to not in valid_teams:
            raise ServiceError("VALIDATION_FAILED", f"알 수 없는 팀: {move.to}", status_code=400)
        seats = session.scalars(
            select(AssignmentReasonORM).where(
                AssignmentReasonORM.plan_id == plan_id,
                AssignmentReasonORM.applicant_id == move.applicant_id,
            )
        ).all()
        row = next((s for s in seats if s.team_name == move.from_), None)
        if row is None:
            raise ServiceError(
                "NOT_FOUND",
                f"배정 없음: applicant_id={move.applicant_id}, team={move.from_}",
                status_code=404,
            )
        if any(s is not row and s.team_name == move.to for s in seats):
            raise ServiceError(
                "VALIDATION_FAILED",
                f"{move.to} 은 이미 applicant_id={move.applicant_id} 를 보고 있습니다",
                status_code=409,
            )
        row.team_name = move.to
        if not row.is_duplicate:
            # 주 팀이 바뀌었으니 같이 보는 자리가 가리키는 이름도 바뀐다
            for other in seats:
                if other is not row and other.primary_team == move.from_:
                    other.primary_team = move.to
        tags = list(row.tags or [])
        if "HR_MANUAL" not in tags:
            tags.append("HR_MANUAL")
        row.tags = tags
        applied.append(
            {
                "applicant_id": move.applicant_id,
                "from": move.from_,
                "to": move.to,
                "reason": move.reason,
            }
        )

    plan.status = "adjusted" if plan.status != "approved" else plan.status
    session.commit()

    publish_adjusted(round_id=plan.round_id, plan_id=plan.plan_id, moves=applied, actor=actor)
    return _plan_summary(plan, _team_counts(session, plan_id))
