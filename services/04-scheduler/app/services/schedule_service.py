"""스케줄 생성·조회·검증·락 오케스트레이션"""
from __future__ import annotations

import time
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.assignment import Assignment
from app.domain.interviewer import Interviewer as InterviewerRow
from app.domain.round_interviewer import RoundInterviewer
from app.domain.schedule import RuleCompliance, Schedule
from app.domain.schemas import (
    ApplicantIn,
    GenerateConstraints,
    GenerateRequest,
    InterviewerIn,
    PlanResult,
)
from app.errors import NotFoundError, ValidationFailed
from app.infrastructure.contracts import (
    DAYS,
    HOURS,
    band_hours,
    band_of,
    normalize_availability,
)
from app.infrastructure.response_client import applicant_source, availability_source
from app.services import duplicate_fix, lock_manager, registry
from app.services.constraint_checker import check_hard_constraints, off_band, soft_penalty
from app.services.rule_evaluator import RULE_KEYS, RuleReport, rule_compliance

METRICS = {
    "schedules_generated_total": 0,
    "assignments_total": 0,
    "hard_violations_total": 0,
    "lock_upgrades_total": 0,
}


# --------------------------------------------------------------------------
# 면접관
# --------------------------------------------------------------------------
def row_to_interviewer(row: InterviewerRow) -> InterviewerIn:
    return InterviewerIn(
        interviewer_id=row.interviewer_id,
        name=row.name or "",
        team=row.team,
        title=row.title or "",
        max_daily=row.max_daily or 6,
        priority=row.priority or 2,
        email=row.email or "",
        availability=normalize_availability(row.availability),
    )


def _merge_availability(
    row: InterviewerRow, responded: dict[str, InterviewerIn]
) -> InterviewerIn:
    """마스터 정보 + 03 회신 가용성을 합친다.

    회신이 없는 사람은 마스터에 저장된 가용성을 쓰고, 그것도 없으면 전 시간대
    가용으로 본다 — 선별해 놓고 회신을 안 했다고 명단에서 빼버리면 시간표가
    통째로 비어버려서, PoC에서는 제약 없음으로 두고 회신 여부는 03에서 본다.

    둘 다 있으면 교집합이다. 부서 화면에서 '앞타임' 을 골라 뒀는데 회신에 늦은
    칸이 섞여 있다고 거기 넣어 버리면, 골라 둔 의미가 없어진다.
    """
    hit = responded.get(row.interviewer_id)
    row_availability = normalize_availability(row.availability)
    if hit is not None and hit.availability:
        merged = normalize_availability(hit.availability)
        if row_availability:
            both = {
                day: [h for h in hours if h in set(row_availability.get(day, []))]
                for day, hours in merged.items()
            }
            both = {day: hours for day, hours in both.items() if hours}
            merged = both or row_availability   # 겹치는 시간이 없으면 부서 뜻을 따른다
        return InterviewerIn(
            interviewer_id=row.interviewer_id,
            name=row.name or hit.name,
            team=row.team or hit.team,
            title=row.title or hit.title,
            max_daily=min(hit.max_daily or 6, row.max_daily or 6),
            priority=row.priority or hit.priority or 2,
            email=row.email or hit.email,
            availability=merged,
        )
    base = row_to_interviewer(row)
    if base.availability:
        return base
    return InterviewerIn(
        interviewer_id=base.interviewer_id,
        name=base.name,
        team=base.team,
        title=base.title,
        max_daily=base.max_daily,
        priority=base.priority,
        email=base.email,
        availability={day: list(HOURS) for day in DAYS},
    )


def load_interviewers(db: Session, round_id: str) -> list[InterviewerIn]:
    """회차 선별 목록 > 면접관 마스터 > 03(또는 목) 순으로 명단을 정한다."""
    from app.services import interviewer_roster

    selected = interviewer_roster.selected_ids(db, round_id)
    if selected:
        rows = db.scalars(
            select(InterviewerRow).where(InterviewerRow.interviewer_id.in_(selected))
        ).all()
        responded = {}
        try:
            # 목으로 메우면 안 되는 자리다. 아래에서 마스터 가용성과 교집합을
            # 잡으므로, 지어낸 시간이 섞이면 실제로 되는 칸이 지워진다.
            responded = {iv.interviewer_id: iv
                         for iv in availability_source.fetch(round_id, allow_mock=False)}
        except Exception:  # 03 미기동 · 회신 0건 → 마스터 가용성으로 진행
            responded = {}
        return [_merge_availability(r, responded) for r in rows]

    rows = db.scalars(select(InterviewerRow)).all()
    if rows:
        return [row_to_interviewer(r) for r in rows]
    # DB가 비어 있으면 Service 03(또는 목)에서 가용성을 받아온다 — 여기는
    # 겹쳐 볼 마스터가 아예 없으니 목으로 메워도 지울 것이 없다
    return availability_source.fetch(round_id)


DEFAULT_TITLES = {1: "수석", 2: "책임"}


def backfill_titles(db: Session) -> int:
    """직급 칸이 생기기 전에 넣어 둔 면접관에게 직급을 채워 준다.

    목 데이터에 있는 사번은 그 직급을 그대로 쓰고, 나머지는 역할(팀장/실무)에서
    가져온다. 직급이 비어 있으면 콘솔의 '이번 회차에 들어갈 담당자' 표가 빈칸으로
    나와 누가 누군지 헷갈린다.
    """
    from app.services import mock_data

    known = {iv.interviewer_id: iv.title for iv in mock_data.build_interviewers()
             if iv.title}
    rows = db.scalars(
        select(InterviewerRow).where(
            (InterviewerRow.title.is_(None)) | (InterviewerRow.title == "")
        )
    ).all()
    for row in rows:
        row.title = known.get(row.interviewer_id) or DEFAULT_TITLES.get(row.priority, "책임")
    if rows:
        db.commit()
    return len(rows)


def rederive_bands(db: Session) -> int:
    """옛 규칙으로 저장해 둔 가능 시간을 지금 덩어리 규칙으로 다시 계산한다.

    앞타임 · 뒤타임을 겹치게 바꾸기 전에는 정오에 걸치는 칸이 어느 쪽에도
    들어가지 않았다. '오전만' 을 고른 사람에게는 1~5타임, '오후만' 을 고른
    사람에게는 7~8타임만 저장되고 6타임은 팀에서 아무도 맡을 수 없는 칸으로
    남았다 — 점심 언저리가 통째로 비는 시간표는 여기서 나온다.

    덩어리 이름은 저장하지 않고 그때그때 되읽는 값이라, 규칙을 바꿔도 이미
    저장된 칸 목록은 옛날 그대로다. 그래서 부팅할 때 한 번 되읽어 지금 규칙의
    칸으로 넓혀 준다. 이 칸에 값을 쓰는 곳은 명단 업로드와 가능 시간 저장뿐이고
    둘 다 덩어리에서 펼친 값이므로, 사람이 칸 하나하나 골라 둔 값을 덮어쓸
    일은 없다 (03 회신은 여기 저장하지 않고 배치할 때 교집합으로 합친다).

    일일최대는 건드리지 않는다. 그 숫자는 명단에 직접 적어 냈거나 화면에서
    줄여 둔 값일 수 있어, 칸이 넓어졌다고 임의로 올리면 그 뜻을 지운다.
    """
    changed = 0
    for row in db.scalars(select(InterviewerRow)).all():
        current = normalize_availability(row.availability or {})
        if not current:
            continue
        hours = band_hours(band_of(current))
        widened = {day: list(hours) for day in current}
        if widened != current:
            row.availability = widened
            changed += 1
    if changed:
        db.commit()
    return changed


def seed_interviewers(db: Session) -> int:
    """PoC 부팅 시 면접관 테이블이 비어 있으면 목 데이터로 채운다"""
    existing = db.scalar(select(InterviewerRow).limit(1))
    if existing is not None:
        return 0
    from app.services import mock_data

    rows = [
        InterviewerRow(
            interviewer_id=iv.interviewer_id,
            name=iv.name,
            title=iv.title,
            team=iv.team,
            max_daily=iv.max_daily,
            priority=iv.priority,
            email=iv.email,
            availability=iv.availability,
        )
        for iv in mock_data.build_interviewers()
    ]
    db.add_all(rows)
    db.commit()
    return len(rows)


# --------------------------------------------------------------------------
# 생성
# --------------------------------------------------------------------------
def generate(db: Session, req: GenerateRequest) -> tuple[Schedule, RuleReport, list[dict], PlanResult]:
    applicants: list[ApplicantIn] = applicant_source.fetch(req.round_id, req.plan_id)
    if not applicants:
        raise ValidationFailed("배치할 지원자가 없습니다", {"round_id": req.round_id})

    interviewers = load_interviewers(db, req.round_id)
    if not interviewers:
        raise ValidationFailed("등록된 면접관이 없습니다", {"round_id": req.round_id})

    # 부서가 정한 짝이 시간표의 입력이다. 짝이 없는 지원자는 시간표에 넣지
    # 않는다 — 넣어 버리면 '면접 못 보는 사람' 명단과 시간표가 어긋난다.
    candidates = applicants
    unmatched: list[ApplicantIn] = []
    constraints = req.constraints
    if req.pairs is not None:
        known = {iv.interviewer_id for iv in interviewers}
        pairs = {a: i for a, i in req.pairs.items() if i in known}
        # 두 팀이 같이 보는 사람은 자리가 둘이고 팀마다 담당자가 다르다 —
        # 지원자 번호 하나로 묶으면 한 팀의 짝이 다른 팀 것에 덮인다.
        by_team = {
            team: {a: i for a, i in (mine or {}).items() if i in known}
            for team, mine in (req.pairs_by_team or {}).items()
        }

        def _paired(seat: ApplicantIn) -> bool:
            mine = by_team.get(seat.team)
            return seat.applicant_id in (pairs if mine is None else mine)

        candidates = [a for a in applicants if _paired(a)]
        unmatched = [a for a in applicants if not _paired(a)]
        if not candidates:
            raise ValidationFailed(
                "부서에서 담당자를 정해 보낸 지원자가 없습니다. "
                "부서 화면의 '② 면접자 담당자 매칭'을 먼저 마쳐야 합니다.",
                {"round_id": req.round_id, "지원자": len(applicants)},
            )
        constraints = req.constraints.model_copy(
            update={"pairs": pairs, "pairs_by_team": by_team}
        )

    # 부서가 자기 시간표에서 잡아 둔 자리도 함께 넘긴다 — 짝만 물려받고 시각은
    # 새로 짜면, 부서가 보고 보낸 시간표와 최종 시간표가 서로 다른 물건이 된다.
    if req.seats_by_team is not None:
        constraints = constraints.model_copy(
            update={"seats_by_team": req.seats_by_team}
        )
    # 인사가 명단을 보낼 때 이미 정한 팀별 면접 요일. 부서는 그 요일 위에서
    # '1일차 · 2일차' 자리를 잡았으므로 여기서 요일을 다시 뽑으면 안 된다.
    if req.days_by_team is not None:
        constraints = constraints.model_copy(
            update={"days_by_team": req.days_by_team}
        )

    started = time.perf_counter()
    plan = registry.run(req.algorithm, candidates, interviewers, constraints)
    elapsed_ms = (time.perf_counter() - started) * 1000
    plan.unassigned = list(plan.unassigned) + unmatched

    report = rule_compliance(
        plan.assignments,
        interviewers,
        applicants,
        grad_ratio_target=req.constraints.grad_ratio_target,
        grad_ratio_tolerance=req.constraints.grad_ratio_tolerance,
    )
    # 인사가 가능 시간을 무시하기로 했으면 그 어긋남은 위반이 아니라 감수한
    # 비용이다. 위반에서 빼는 대신 어긋난 자리를 따로 적어 둔다.
    ignore_availability = bool(req.constraints.ignore_availability)
    violations = check_hard_constraints(
        plan.assignments, interviewers, ignore_availability=ignore_availability
    )
    off_band_rows = off_band(plan.assignments, interviewers) if ignore_availability else []
    coverage = 100.0 * len(plan.assignments) / len(applicants)

    schedule = Schedule(
        round_id=req.round_id,
        plan_id=req.plan_id,
        status="draft",
        algorithm=plan.algorithm,
        total_assigned=len(plan.assignments),
        total_applicants=len(applicants),
        coverage_pct=round(coverage, 1),
        hard_violations=len(violations),
        overall_score=report.overall,
        elapsed_ms=round(elapsed_ms, 2),
        generated_by=req.generated_by,
    )
    db.add(schedule)
    db.flush()

    for planned in plan.assignments:
        db.add(
            Assignment(
                schedule_id=schedule.schedule_id,
                applicant_id=planned.applicant_id,
                applicant_name=planned.applicant_name,
                interviewer_id=planned.interviewer_id,
                team=planned.team,
                degree=planned.degree,
                day=planned.day,
                hour=planned.hour,
                lock_level="DRAFT",
                reason_tags=list(planned.reason_tags),
            )
        )

    for key in RULE_KEYS:
        db.add(
            RuleCompliance(
                schedule_id=schedule.schedule_id,
                rule_name=key,
                score=report.scores[key],
                details=report.details.get(key, {}),
            )
        )
    db.add(
        RuleCompliance(
            schedule_id=schedule.schedule_id,
            rule_name="overall",
            score=report.overall,
            details={
                "coverage_pct": round(coverage, 1),
                "unassigned": [a.applicant_id for a in plan.unassigned],
                "notes": _jsonable(plan.notes),
                "elapsed_ms": round(elapsed_ms, 2),
                # 다시 검증할 때도 같은 잣대를 쓰도록 남겨 둔다 (schedules 표에
                # 칸을 늘리면 이미 쓰던 DB 를 못 읽으므로 여기에 적는다).
                "ignore_availability": ignore_availability,
                "off_band": _jsonable(off_band_rows),
            },
        )
    )
    db.commit()
    db.refresh(schedule)

    METRICS["schedules_generated_total"] += 1
    METRICS["assignments_total"] += len(plan.assignments)
    METRICS["hard_violations_total"] += len(violations)
    return schedule, report, violations, plan, off_band_rows


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------
def get_schedule(db: Session, schedule_id: str) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise NotFoundError(f"스케줄을 찾을 수 없습니다: {schedule_id}")
    return schedule


def reset_round(db: Session, round_id: str) -> dict:
    """그 회차의 시간표를 전부 지운다 (배정 · 규칙 점수까지).

    1단계에서 지원자 명단을 다시 올리면 그 명단으로 짠 시간표는 무효다.
    남겨 두면 콘솔 ③ 이 회차 목록의 최신 것을 집어 지운 명단의 시간표를
    '지금 상태' 로 보여 준다. 회차별 면접관 선별도 함께 지운다 — 면접관
    마스터(interviewers)는 회차와 무관하므로 건드리지 않는다.
    """
    schedule_ids = list(
        db.scalars(select(Schedule.schedule_id).where(Schedule.round_id == round_id))
    )
    assignments = rules = schedules = 0
    if schedule_ids:
        assignments = (
            db.query(Assignment).filter(Assignment.schedule_id.in_(schedule_ids))
            .delete(synchronize_session=False)
        )
        rules = (
            db.query(RuleCompliance).filter(RuleCompliance.schedule_id.in_(schedule_ids))
            .delete(synchronize_session=False)
        )
        schedules = (
            db.query(Schedule).filter(Schedule.round_id == round_id)
            .delete(synchronize_session=False)
        )
    picked = (
        db.query(RoundInterviewer).filter(RoundInterviewer.round_id == round_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {
        "round_id": round_id,
        "deleted_schedules": schedules,
        "deleted_assignments": assignments,
        "deleted_rules": rules,
        "deleted_round_interviewers": picked,
    }


def list_schedules_for_round(db: Session, round_id: str) -> list[dict]:
    """그 회차에 실제로 남아 있는 시간표 목록 (최신순).

    콘솔은 그동안 감사 로그의 SCHEDULE_GENERATED 기록으로 시간표를 골랐는데,
    기록은 남아도 시간표가 지워졌거나 DB 를 갈아 끼우면 '스케줄을 찾을 수
    없습니다' 만 나온다. 화면이 실제 목록을 물어볼 수 있게 열어 둔다.
    """
    rows = db.scalars(
        select(Schedule)
        .where(Schedule.round_id == round_id)
        .order_by(Schedule.generated_at.desc())
    ).all()
    return [
        {
            "schedule_id": row.schedule_id,
            "round_id": row.round_id,
            "plan_id": row.plan_id,
            "status": row.status,
            "algorithm": row.algorithm,
            "total_assigned": row.total_assigned,
            "total_applicants": row.total_applicants,
            "coverage_pct": row.coverage_pct,
            "hard_violations": row.hard_violations,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "generated_by": row.generated_by,
        }
        for row in rows
    ]


def list_assignments(db: Session, schedule_id: str) -> list[Assignment]:
    return list(
        db.scalars(
            select(Assignment)
            .where(Assignment.schedule_id == schedule_id)
            .order_by(Assignment.day, Assignment.hour, Assignment.team)
        ).all()
    )


def heatmap(assignments: list[Assignment]) -> dict:
    grid = {day: {hour: 0 for hour in HOURS} for day in DAYS}
    teams: dict[str, dict[str, list[str]]] = {day: {hour: [] for hour in HOURS} for day in DAYS}
    for a in assignments:
        if a.day in grid and a.hour in grid[a.day]:
            grid[a.day][a.hour] += 1
            teams[a.day][a.hour].append(a.team)
    peak = max((c for row in grid.values() for c in row.values()), default=0)
    return {
        "days": DAYS,
        "hours": HOURS,
        "grid": grid,
        "teams": teams,
        "peak": peak,
        "total": len(assignments),
    }


def by_team(assignments: list[Assignment]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for a in sorted(assignments, key=lambda x: (x.team, DAYS.index(x.day) if x.day in DAYS else 9, x.hour)):
        grouped[a.team].append(
            {
                "assignment_id": a.assignment_id,
                "applicant_id": a.applicant_id,
                "applicant_name": a.applicant_name,
                "interviewer_id": a.interviewer_id,
                "degree": a.degree,
                "day": a.day,
                "hour": a.hour,
                "lock_level": a.lock_level,
                "reason_tags": a.reason_tags,
            }
        )
    return {
        "teams": {team: rows for team, rows in sorted(grouped.items())},
        "counts": {team: len(rows) for team, rows in sorted(grouped.items())},
    }


def stored_rules(db: Session, schedule_id: str) -> dict:
    rows = db.scalars(
        select(RuleCompliance).where(RuleCompliance.schedule_id == schedule_id)
    ).all()
    if not rows:
        raise NotFoundError(f"규칙 준수율 기록이 없습니다: {schedule_id}")
    return {
        row.rule_name: {"score": row.score, "detail": row.details or {}}
        for row in sorted(rows, key=lambda r: r.rule_name)
    }


# --------------------------------------------------------------------------
# 검증 · 락
# --------------------------------------------------------------------------
def ignored_availability(db: Session, schedule_id: str) -> bool:
    """만들 때 '담당자 일정 무시하고 배치하기'를 켰는지 — 검증도 같은 잣대로."""
    row = db.scalar(
        select(RuleCompliance).where(
            RuleCompliance.schedule_id == schedule_id,
            RuleCompliance.rule_name == "overall",
        )
    )
    return bool((row.details or {}).get("ignore_availability")) if row else False


def validate_schedule(
    db: Session, schedule_id: str
) -> tuple[Schedule, list[dict], float, list[dict]]:
    schedule = get_schedule(db, schedule_id)
    assignments = list_assignments(db, schedule_id)
    interviewers = load_interviewers(db, schedule.round_id)
    ignore = ignored_availability(db, schedule_id)
    violations = check_hard_constraints(assignments, interviewers, ignore_availability=ignore)
    report = rule_compliance(assignments, interviewers)
    penalty = soft_penalty(report, assignments, interviewers)
    off_band_rows = off_band(assignments, interviewers) if ignore else []

    schedule.hard_violations = len(violations)
    db.commit()
    return schedule, violations, penalty, off_band_rows


def fix_duplicates(
    db: Session,
    schedule_id: str,
    days_by_team: dict | None = None,
    actor: str = "system",
) -> dict:
    """두 팀 면접이 같은 시각에 잡힌 사람만 옮긴다 — 나머지는 그대로 둔다.

    부서가 확인하고 보낸 시간표를 인사가 통째로 다시 짜지 않게 하려는 것이다.
    옮긴 자리에는 왜 옮겼는지 사유를 남기고, 못 옮긴 자리는 그 까닭과 함께
    돌려준다 — 자리를 못 찾았다는 말 없이 조용히 겹친 채로 두지 않는다.
    """
    schedule = get_schedule(db, schedule_id)
    assignments = list_assignments(db, schedule_id)
    interviewers = load_interviewers(db, schedule.round_id)
    ignore = ignored_availability(db, schedule_id)

    before = duplicate_fix.conflicts(assignments)
    moved, stuck = duplicate_fix.plan_fix(
        assignments, interviewers, days_by_team, ignore_availability=ignore
    )
    for move in moved:
        row = assignments[move["index"]]
        row.day, row.hour = move["day"], move["hour"]
        tags = list(row.reason_tags or [])
        if duplicate_fix.FIX_TAG not in tags:
            tags.append(duplicate_fix.FIX_TAG)
        row.reason_tags = tags

    violations = check_hard_constraints(
        assignments, interviewers, ignore_availability=ignore
    )
    schedule.hard_violations = len(violations)
    db.commit()

    return {
        "schedule_id": schedule_id,
        "round_id": schedule.round_id,
        "actor": actor,
        "conflicts_before": before,
        "conflicts_after": duplicate_fix.conflicts(assignments),
        "moved": moved,
        "stuck": stuck,
        "hard_violations": len(violations),
    }


def lock(
    db: Session, schedule_id: str, requested: str, applicant_ids: list[str] | None
) -> tuple[Schedule, list[Assignment]]:
    schedule = get_schedule(db, schedule_id)
    assignments = list_assignments(db, schedule_id)
    if not assignments:
        raise ValidationFailed("배정이 없는 스케줄은 락할 수 없습니다", {"schedule_id": schedule_id})

    try:
        changed = lock_manager.apply_lock(assignments, requested, applicant_ids)
    except lock_manager.LockTransitionError as exc:
        raise ValidationFailed(
            str(exc), {"current": exc.current, "requested": exc.requested}
        ) from exc

    schedule.status = lock_manager.schedule_status_for([a.lock_level for a in assignments])
    db.commit()
    METRICS["lock_upgrades_total"] += 1
    return schedule, changed


def schedule_payload(schedule: Schedule, report_scores: dict, violations: list[dict]) -> dict:
    return {
        "schedule_id": schedule.schedule_id,
        "round_id": schedule.round_id,
        "plan_id": schedule.plan_id,
        "algorithm": schedule.algorithm,
        "status": schedule.status,
        "total_assigned": schedule.total_assigned,
        "total_applicants": schedule.total_applicants,
        "coverage_pct": schedule.coverage_pct,
        "hard_violations": len(violations),
        "elapsed_ms": schedule.elapsed_ms,
        "rule_compliance": report_scores,
    }
