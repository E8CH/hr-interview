"""여러 취합본을 대조하고 하나로 합치는 로직.

콘솔 1번 메뉴(자료취합 버전관리)가 쓰는 경로다. 팀별로 따로 손댄 취합본이
여러 개 올라오면
  - 마스터끼리는 같은 지원자의 셀 값이 어긋난 곳을 집어내고(대조),
  - 마스터↔팀배포본은 중복/미배포/마스터에 없는 사람을 집어내고(무결성),
  - 담당자가 행마다 채택할 버전을 고르면 최종 파일을 만들어 새 마스터로 등록한다.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Version
from app.services import version_service as svc
from app.services.excel_table import (
    ID_HEADER,
    NAME_HEADER,
    SheetTable,
    TableError,
    norm,
    read_table,
    write_table,
)
from app.services.integrity_checker import check_integrity

KIND_MASTER = svc.KIND_MASTER
KIND_TEAM = svc.KIND_TEAM

# 배포본 파일명 규칙: 희망지원자_{팀}.xlsx
_TEAM_FILE = re.compile(r"희망지원자[_\-\s]*(?P<team>.+)$")

#: 취합본에 새기는 팀 나눔 컬럼. 두 팀 이상이 같은 사람을 적어 냈으면 쉼표로 잇는다.
#: 02(배포)가 같은 이름으로 읽어 배정을 만든다 — 이름을 바꾸면 양쪽을 함께 고쳐야 한다.
TEAM_HEADER = "담당팀"
TEAM_SEPARATOR = ", "

# 값이 어긋나도 알릴 필요가 없는 컬럼 (파일마다 관례적으로 달라지는 것들)
IGNORED_COLUMNS = {"", "순번", "No", "no"}


def classify_file(file_name: str) -> tuple[str, str | None]:
    """파일명으로 마스터/팀배포본을 가른다.

    헤더는 마스터와 배포본이 사실상 같아서(둘 다 취합파일에서 잘라낸 것) 내용으로는
    구분이 안 된다. 실제 운영에서 배포본만 `희망지원자_{팀}.xlsx` 로 이름이 붙으므로
    그걸 신호로 쓴다. 콘솔에서 담당자가 수동으로 바꿀 수 있게 열어 둔다.
    """
    stem = Path(file_name or "").stem.strip()
    match = _TEAM_FILE.search(stem)
    if match:
        team = match.group("team").strip(" _-")
        return KIND_TEAM, team or "미상"
    return KIND_MASTER, None


def _load_table(version: Version) -> SheetTable:
    data, _ = svc.read_file_of(version)
    try:
        return read_table(data)
    except Exception as exc:  # 손상 파일 · 엑셀 아님 · 헤더 없음
        raise TableError(f"{version.file_name}: {exc}") from exc


def _version_brief(version: Version) -> dict:
    return {
        "version_id": version.version_id,
        "round_id": version.round_id,
        "kind": version.kind,
        "team_name": version.team_name,
        "file_name": version.file_name,
        "fingerprint": version.fingerprint,
        "applicant_count": version.applicant_count,
        "actor": version.actor,
        "created_at": version.created_at,
    }


def compare_versions(session: Session, version_ids: list[str]) -> dict:
    """마스터끼리 셀 대조 + 마스터↔팀배포본 무결성 검사를 한 번에 돌린다."""
    if len(version_ids) < 1:
        raise svc.VersionError("VALIDATION_FAILED", "version_ids 가 비어 있습니다")

    versions = [svc.get_by_id(session, vid) for vid in version_ids]
    masters = [v for v in versions if v.kind == KIND_MASTER]
    teams = [v for v in versions if v.kind == KIND_TEAM]

    tables: dict[str, SheetTable] = {}
    unreadable: list[dict] = []
    for version in versions:
        try:
            tables[version.version_id] = _load_table(version)
        except (TableError, svc.VersionError) as exc:
            unreadable.append({"version_id": version.version_id, "reason": str(exc)})

    master_ids = [v.version_id for v in masters if v.version_id in tables]
    conflicts, only_in, identical = _compare_masters(
        {vid: tables[vid] for vid in master_ids}
    )

    integrity = None
    if masters and teams:
        base = masters[0]
        team_map: dict[str, list[str]] = {}
        for version in teams:
            key = version.team_name or version.file_name
            team_map[key] = list(version.applicant_ids)
        integrity = check_integrity(list(base.applicant_ids), team_map)
        integrity["master_version_id"] = base.version_id

    # 마스터가 하나도 없으면(배포본만 올린 회차) 읽힌 파일 전부가 병합 대상이 된다.
    readable_ids = [v.version_id for v in versions if v.version_id in tables]

    return {
        "versions": [_version_brief(v) for v in versions],
        "master_version_ids": [v.version_id for v in masters],
        "mergeable_version_ids": master_ids or readable_ids,
        "team_version_ids": [v.version_id for v in teams],
        "unreadable": unreadable,
        "conflicts": conflicts,
        "conflict_count": len(conflicts),
        "only_in": only_in,
        "identical_count": identical,
        "integrity": integrity,
    }


def _compare_masters(
    tables: dict[str, SheetTable],
) -> tuple[list[dict], dict[str, list[str]], int]:
    """지원자별로 같은 컬럼의 값이 갈리는 곳을 모은다."""
    if len(tables) < 2:
        # 대조 상대가 없으면 충돌도 없다 (무결성 검사만 의미가 있다)
        return [], {vid: [] for vid in tables}, 0

    maps = {vid: table.by_id() for vid, table in tables.items()}
    order: list[str] = []
    for vid in tables:
        for aid in tables[vid].ids():
            if aid not in order:
                order.append(aid)

    conflicts: list[dict] = []
    only_in: dict[str, list[str]] = {vid: [] for vid in tables}
    identical = 0

    for aid in order:
        present = [vid for vid in tables if aid in maps[vid]]
        if len(present) == 1:
            only_in[present[0]].append(aid)
            continue

        rows = {vid: tables[vid].row_map(maps[vid][aid]) for vid in present}
        columns: list[str] = []
        for vid in present:
            for name in rows[vid]:
                if name not in columns and name not in IGNORED_COLUMNS:
                    columns.append(name)

        fields = []
        for column in columns:
            values = {vid: rows[vid].get(column, "") for vid in present}
            if len(set(values.values())) > 1:
                fields.append({"column": column, "values": values})

        if not fields:
            identical += 1
            continue

        first = rows[present[0]]
        conflicts.append({
            "applicant_id": aid,
            "name": first.get(NAME_HEADER, ""),
            "present_in": present,
            "fields": fields,
            "candidates": rows,
        })

    return conflicts, only_in, identical


def team_assignment_map(session: Session, round_id: str) -> dict[str, list[str]]:
    """지원자 번호 → 그 사람을 적어 낸 팀들 (팀 이름 가나다순).

    팀별 배포본은 팀 이름이 파일명에만 있고 헤더는 마스터와 한 글자도 다르지 않다.
    그래서 병합할 때 여기서 읽어 취합본에 새겨 두지 않으면, 부서가 나눈 팀은
    1단계를 지나는 순간 사라진다 — 그 공백 때문에 2단계에서 본 명단과 부서가
    받는 명단이 갈라졌다.
    """
    versions = session.scalars(
        select(Version).where(
            Version.round_id == round_id,
            Version.kind == KIND_TEAM,
            Version.is_active.is_(True),
        )
    ).all()

    out: dict[str, list[str]] = {}
    for version in sorted(versions, key=lambda v: v.team_name or v.file_name or ""):
        team = (version.team_name or "").strip()
        if not team:
            continue
        for applicant_id in version.applicant_ids or []:
            teams = out.setdefault(norm(applicant_id), [])
            if team not in teams:
                teams.append(team)
    return out


def _write_team_column(
    base: SheetTable, rows: list[list], teams_of: dict[str, list[str]]
) -> tuple[SheetTable, list[list]]:
    """행마다 '담당팀' 을 채운다 — 컬럼이 이미 있으면 덮고, 없으면 뒤에 붙인다.

    덮어쓰는 쪽이 중요하다. 한 번 병합한 취합본을 다시 기준으로 삼는 경우가 있어
    붙이기만 하면 같은 이름의 컬럼이 늘어나고, 뒤엣것은 아무도 읽지 않는다.
    """
    header = list(base.header)
    columns = {norm(name): index for index, name in enumerate(header) if norm(name)}
    target = columns.get(TEAM_HEADER)
    if target is None:
        target = len(header)
        header.append(TEAM_HEADER)

    width = len(header)
    filled: list[list] = []
    for row in rows:
        padded = list(row)[:width]
        padded += [None] * (width - len(padded))
        applicant_id = norm(row[base.id_col]) if base.id_col < len(row) else ""
        padded[target] = TEAM_SEPARATOR.join(teams_of.get(applicant_id, []))
        filled.append(padded)
    return replace(base, header=header), filled


def _project(row: list, source: SheetTable, base: SheetTable) -> list:
    """다른 파일의 행을 기준 파일의 컬럼 순서에 맞춰 옮겨 담는다."""
    if source is base:
        return list(row)
    src = source.columns
    out = []
    for name in base.header:
        index = src.get(norm(name))
        out.append(row[index] if index is not None and index < len(row) else None)
    return out


def merge_versions(
    session: Session,
    *,
    round_id: str,
    base_version_id: str,
    version_ids: list[str],
    selections: dict[str, str],
    actor: str,
    file_name: str | None = None,
) -> dict:
    """행마다 채택할 버전을 받아 최종 취합본을 만들고 새 마스터로 등록한다.

    selections 에 없는 지원자는 기준(base) 파일의 행을 그대로 쓴다. 기준에도
    없으면 version_ids 순서상 먼저 나온 파일의 행을 쓴다.

    마스터가 하나도 없는 회차(배포본만 올린 경우)는 넘어온 파일들을 그대로 합쳐
    최종 취합본을 만든다 — 등록한 파일이 곧 이 회차의 전부이기 때문이다.
    """
    ids = list(dict.fromkeys([base_version_id, *version_ids]))
    versions = {vid: svc.get_by_id(session, vid) for vid in ids}
    has_master = any(v.kind == KIND_MASTER for v in versions.values())
    if has_master and versions[base_version_id].kind != KIND_MASTER:
        raise svc.VersionError("VALIDATION_FAILED", "기준 버전은 마스터여야 합니다")

    tables: dict[str, SheetTable] = {}
    for vid, version in versions.items():
        if has_master and version.kind != KIND_MASTER:
            continue
        try:
            tables[vid] = _load_table(version)
        except TableError as exc:
            if vid == base_version_id:
                raise svc.VersionError("VALIDATION_FAILED", f"기준 파일을 읽을 수 없습니다 — {exc}")
            # 읽을 수 없는 후보는 채택 대상에서 빠지고 병합은 계속된다
            continue
    base = tables[base_version_id]
    maps = {vid: table.by_id() for vid, table in tables.items()}

    order: list[str] = list(base.ids())
    for vid in tables:
        for aid in tables[vid].ids():
            if aid not in order:
                order.append(aid)

    merged_rows: list[list] = []
    used: dict[str, int] = {vid: 0 for vid in tables}
    unresolved: list[str] = []
    for aid in order:
        chosen = selections.get(aid)
        if chosen not in maps or aid not in maps.get(chosen, {}):
            chosen = None
        if chosen is None:
            for vid in [base_version_id, *[v for v in tables if v != base_version_id]]:
                if aid in maps[vid]:
                    chosen = vid
                    break
        if chosen is None:
            unresolved.append(aid)
            continue
        used[chosen] += 1
        merged_rows.append(_project(maps[chosen][aid], tables[chosen], base))

    # 팀 나눔을 취합본에 새긴다 — 이 컬럼이 이후 모든 단계의 팀 기준이 된다
    teams_of = team_assignment_map(session, round_id)
    merged_ids = [
        norm(row[base.id_col]) for row in merged_rows if base.id_col < len(row)
    ]
    out_table, merged_rows = _write_team_column(base, merged_rows, teams_of)

    data = write_table(out_table, merged_rows)
    name = file_name or f"취합_최종_{round_id}.xlsx"
    version = svc.register_version(
        session,
        file_bytes=data,
        file_name=name,
        round_id=round_id,
        kind=KIND_MASTER,
        actor=actor,
    )
    return {
        "version_id": version.version_id,
        "file_name": version.file_name,
        "fingerprint": version.fingerprint,
        "applicant_count": version.applicant_count,
        "row_count": len(merged_rows),
        "rows_from": used,
        "unresolved": unresolved,
        "source_version_ids": ids,
        "created_at": version.created_at,
        # 새겨 넣은 팀 나눔 — 화면이 "몇 명에게 팀이 붙었는지" 를 바로 보여 준다
        "team_column": TEAM_HEADER,
        "teamed_count": sum(1 for aid in merged_ids if teams_of.get(aid)),
        "team_duplicate_count": sum(
            1 for aid in merged_ids if len(teams_of.get(aid) or []) > 1
        ),
        "teamless": [aid for aid in merged_ids if not teams_of.get(aid)],
    }


def preview_merged(session: Session, version_id: str, limit: int = 50) -> dict:
    """생성된 최종 파일을 표로 미리 본다 (콘솔 확인용)."""
    version = svc.get_by_id(session, version_id)
    table = _load_table(version)
    columns = [norm(c) for c in table.header if norm(c)]
    rows = [table.row_map(row) for row in table.rows[:limit]]
    return {
        "version_id": version_id,
        "file_name": version.file_name,
        "columns": columns,
        "total_rows": len(table.rows),
        "rows": rows,
        "id_column": ID_HEADER,
    }
