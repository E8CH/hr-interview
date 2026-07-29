"""버전 관리 오케스트레이션 — DB + 스토리지 + 이벤트.

명세 API 6종의 비즈니스 로직을 담당.
"""
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import events
from app.domain.models import IntegrityCheck, Version
from app.infrastructure import storage
from app.services.excel_parser import extract_applicant_ids
from app.services.fingerprint import compute_fingerprint
from app.services.integrity_checker import STATUS_ISSUES, check_integrity

KIND_MASTER = "master"
KIND_TEAM = "team_distribution"


class VersionError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _new_version_id() -> str:
    return f"vm_{uuid4().hex[:12]}"


def _active_query(round_id: str, kind: str, team_name: str | None):
    stmt = select(Version).where(
        Version.round_id == round_id,
        Version.kind == kind,
        Version.is_active.is_(True),
    )
    if kind == KIND_TEAM:
        stmt = stmt.where(Version.team_name == team_name)
    return stmt


def get_active(session: Session, round_id: str, kind: str,
               team_name: str | None = None) -> Version | None:
    stmt = _active_query(round_id, kind, team_name).order_by(Version.created_at.desc())
    return session.scalars(stmt).first()


def get_by_id(session: Session, version_id: str) -> Version:
    version = session.get(Version, version_id)
    if version is None:
        raise VersionError("NOT_FOUND", f"version_id={version_id} 없음", status_code=404)
    return version


def read_file(session: Session, version_id: str) -> tuple[bytes, str]:
    """등록 때 저장해 둔 원본 파일 그대로 돌려준다.

    02(배포)가 "이 회차에 실제로 올라온 그 파일"을 다시 파싱하기 위해 쓴다.
    01은 지원자 번호만 뽑아 두므로 상세 컬럼은 원본에서 읽어야 한다.
    """
    version = get_by_id(session, version_id)
    path = Path(version.file_path)
    if not path.is_file():
        raise VersionError(
            "NOT_FOUND", f"저장된 원본 파일이 없습니다: {path}", status_code=404
        )
    return path.read_bytes(), version.file_name


def register_version(session: Session, *, file_bytes: bytes, file_name: str,
                     round_id: str, kind: str, actor: str,
                     team_name: str | None = None) -> Version:
    if kind not in (KIND_MASTER, KIND_TEAM):
        raise VersionError("VALIDATION_FAILED", f"invalid kind: {kind}")
    if kind == KIND_TEAM and not team_name:
        raise VersionError("VALIDATION_FAILED", "team_name is required for team_distribution")
    if not file_bytes:
        raise VersionError("VALIDATION_FAILED", "empty file")

    effective_team = team_name if kind == KIND_TEAM else None

    fingerprint = compute_fingerprint(file_bytes)
    applicant_ids = extract_applicant_ids(file_bytes)
    file_path = storage.save_file(round_id, kind, effective_team, fingerprint, file_name, file_bytes)

    # 기존 활성본 비활성화 + parent 참조
    previous = get_active(session, round_id, kind, effective_team)
    parent_version = previous.version_id if previous else None
    if previous:
        previous.is_active = False

    version = Version(
        version_id=_new_version_id(),
        round_id=round_id,
        kind=kind,
        team_name=effective_team,
        file_name=file_name,
        file_path=file_path,
        fingerprint=fingerprint,
        applicant_count=len(applicant_ids),
        applicant_ids=applicant_ids,
        actor=actor,
        parent_version=parent_version,
        is_active=True,
    )
    session.add(version)
    session.commit()
    session.refresh(version)

    # 이벤트 발행
    if kind == KIND_MASTER:
        events.emit_master_registered(
            round_id=round_id, version_id=version.version_id,
            fingerprint=fingerprint, applicant_count=version.applicant_count, actor=actor,
        )
    else:
        events.emit_distribution_registered(
            round_id=round_id, version_id=version.version_id, team_name=effective_team,
            fingerprint=fingerprint, applicant_count=version.applicant_count, actor=actor,
        )
    return version


def verify_round(session: Session, round_id: str) -> dict:
    master = get_active(session, round_id, KIND_MASTER)
    master_ids = master.applicant_ids if master else None

    team_versions = session.scalars(
        select(Version).where(
            Version.round_id == round_id,
            Version.kind == KIND_TEAM,
            Version.is_active.is_(True),
        )
    ).all()
    team_map = {v.team_name: list(v.applicant_ids) for v in team_versions}

    result = check_integrity(master_ids, team_map)

    check = IntegrityCheck(
        check_id=str(uuid4()),
        round_id=round_id,
        status=result["status"],
        master_count=result["master_count"],
        distributed_count=result["distributed_count"],
        undistributed_count=result["undistributed_count"],
        duplicate_count=result["duplicate_count"],
        issues=result["issues"],
    )
    session.add(check)
    session.commit()

    if result["status"] == STATUS_ISSUES:
        events.emit_integrity_violated(
            round_id=round_id,
            status=result["status"],
            duplicate_count=result["duplicate_count"],
            undistributed_count=result["undistributed_count"],
            issues=result["issues"],
        )
    return result


def get_history(session: Session, round_id: str) -> list[Version]:
    return list(session.scalars(
        select(Version).where(Version.round_id == round_id).order_by(Version.created_at.desc())
    ).all())


def rollback(session: Session, version_id: str) -> Version:
    target = session.get(Version, version_id)
    if target is None:
        raise VersionError("NOT_FOUND", f"version not found: {version_id}", status_code=404)

    # 같은 (round, kind, team)의 현재 활성본 비활성화
    current = get_active(session, target.round_id, target.kind, target.team_name)
    if current and current.version_id != target.version_id:
        current.is_active = False
    target.is_active = True
    session.commit()
    session.refresh(target)
    return target


def diff(session: Session, from_id: str, to_id: str) -> dict:
    v_from = session.get(Version, from_id)
    v_to = session.get(Version, to_id)
    if v_from is None:
        raise VersionError("NOT_FOUND", f"version not found: {from_id}", status_code=404)
    if v_to is None:
        raise VersionError("NOT_FOUND", f"version not found: {to_id}", status_code=404)

    from_set = set(v_from.applicant_ids)
    to_set = set(v_to.applicant_ids)
    added = [i for i in v_to.applicant_ids if i not in from_set]
    removed = [i for i in v_from.applicant_ids if i not in to_set]
    unchanged = from_set & to_set
    return {
        "added_ids": added,
        "removed_ids": removed,
        "unchanged_count": len(unchanged),
    }
