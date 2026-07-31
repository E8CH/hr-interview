"""다중 파일 대조·병합 (콘솔 1번 메뉴가 쓰는 경로).

여기서는 개인정보가 없는 합성 샘플(tools/fixtures/master_sample.xlsx)만 쓴다.
실제 취합파일 픽스처는 저장소에 올리지 않으므로 새로 클론해도 이 파일은 돈다.
"""
import io
from pathlib import Path

import pytest

from app.services.excel_table import norm, read_table, write_table
from app.services.merge_service import classify_file

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SAMPLE = Path(__file__).resolve().parents[3] / "tools" / "fixtures" / "master_sample.xlsx"


@pytest.fixture(scope="module")
def sample_bytes():
    if not SAMPLE.is_file():
        pytest.skip(f"합성 샘플 없음: {SAMPLE}")
    return SAMPLE.read_bytes()


def _variant(data: bytes, *, column: str, new_value: str, row_index: int = 0) -> bytes:
    """한 셀만 다른 사본을 만든다 — 대조에서 그 한 곳만 잡혀야 한다."""
    table = read_table(data)
    col = table.columns[column]
    rows = [list(r) for r in table.rows]
    while len(rows[row_index]) <= col:
        rows[row_index].append(None)
    rows[row_index][col] = new_value
    return write_table(table, rows)


def _register(client, name, data, round_id, actor="pytest"):
    r = client.post(
        "/api/v1/versions/register-batch",
        files=[("files", (name, io.BytesIO(data), XLSX))],
        data={"round_id": round_id, "actor": actor},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["registered"][0]


# ------------------------------------------------------------------ 분류

@pytest.mark.parametrize("name,kind,team", [
    ("희망지원자_AI솔루션팀.xlsx", "team_distribution", "AI솔루션팀"),
    ("2026_희망지원자_배터리기술팀.xlsx", "team_distribution", "배터리기술팀"),
    ("취합파일.xlsx", "master", None),
    ("master_sample.xlsx", "master", None),
])
def test_classify_file(name, kind, team):
    assert classify_file(name) == (kind, team)


# ------------------------------------------------------------------ 표 읽기/쓰기

def test_round_trip_preserves_columns(sample_bytes):
    """병합 결과물이 원본 컬럼을 하나도 잃지 않는지 — 생년월일 같은 미사용 컬럼 포함."""
    table = read_table(sample_bytes)
    again = read_table(write_table(table, table.rows))
    assert [norm(c) for c in again.header] == [norm(c) for c in table.header]
    assert again.ids() == table.ids()
    assert again.preamble == table.preamble


def test_reader_skips_repeated_header_rows(sample_bytes):
    table = read_table(sample_bytes)
    assert table.header_row == 1
    assert all(table.applicant_id(r) != "지원자 번호" for r in table.rows)
    assert len(table.ids()) == len(table.rows)


# ------------------------------------------------------------------ 대조

def test_compare_finds_only_the_changed_cell(client, sample_bytes, sample_round_id):
    original = _register(client, "취합_A.xlsx", sample_bytes, sample_round_id)
    changed = _register(
        client, "취합_B.xlsx",
        _variant(sample_bytes, column="1지망_조직", new_value="제9기술원"),
        sample_round_id,
    )

    r = client.post("/api/v1/versions/compare", json={
        "version_ids": [original["version_id"], changed["version_id"]],
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    assert data["conflict_count"] == 1
    conflict = data["conflicts"][0]
    assert [f["column"] for f in conflict["fields"]] == ["1지망_조직"]
    assert conflict["fields"][0]["values"][changed["version_id"]] == "제9기술원"
    assert set(data["only_in"][original["version_id"]]) == set()
    assert data["identical_count"] == len(read_table(sample_bytes).ids()) - 1


def test_compare_reports_rows_only_in_one_file(client, sample_bytes, sample_round_id):
    table = read_table(sample_bytes)
    trimmed = write_table(table, table.rows[:-3])
    dropped = table.ids()[-3:]

    full = _register(client, "취합_전체.xlsx", sample_bytes, sample_round_id)
    short = _register(client, "취합_일부.xlsx", trimmed, sample_round_id)

    data = client.post("/api/v1/versions/compare", json={
        "version_ids": [full["version_id"], short["version_id"]],
    }).json()["data"]
    assert set(data["only_in"][full["version_id"]]) == set(dropped)
    assert data["only_in"][short["version_id"]] == []


def test_compare_master_and_team_runs_integrity(client, sample_bytes, sample_round_id):
    table = read_table(sample_bytes)
    half = write_table(table, table.rows[:10])

    master = _register(client, "취합파일.xlsx", sample_bytes, sample_round_id)
    team = _register(client, "희망지원자_AI솔루션팀.xlsx", half, sample_round_id)
    assert team["kind"] == "team_distribution"
    assert team["team_name"] == "AI솔루션팀"

    data = client.post("/api/v1/versions/compare", json={
        "version_ids": [master["version_id"], team["version_id"]],
    }).json()["data"]
    integrity = data["integrity"]
    assert integrity is not None
    assert integrity["distributed_count"] == 10
    assert integrity["undistributed_count"] == master["applicant_count"] - 10
    assert integrity["status"] == "ISSUES_FOUND"


# ------------------------------------------------------------------ 병합

def test_merge_adopts_selected_row(client, sample_bytes, sample_round_id):
    table = read_table(sample_bytes)
    target_id = table.ids()[0]

    base = _register(client, "취합_A.xlsx", sample_bytes, sample_round_id)
    other = _register(
        client, "취합_B.xlsx",
        _variant(sample_bytes, column="1지망_조직", new_value="제9기술원"),
        sample_round_id,
    )

    r = client.post("/api/v1/versions/merge", json={
        "round_id": sample_round_id,
        "base_version_id": base["version_id"],
        "version_ids": [other["version_id"]],
        "selections": {target_id: other["version_id"]},
        "actor": "pytest",
    })
    assert r.status_code == 201, r.text
    merged = r.json()["data"]
    assert merged["row_count"] == len(table.ids())
    assert merged["rows_from"][other["version_id"]] == 1
    assert merged["unresolved"] == []

    preview = client.get(
        f"/api/v1/versions/by-id/{merged['version_id']}/preview", params={"limit": 5}
    ).json()["data"]
    first = preview["rows"][0]
    assert first["지원자 번호"] == target_id
    assert first["1지망_조직"] == "제9기술원"
    # 나머지 행은 기준 파일 그대로여야 한다
    assert preview["rows"][1]["1지망_조직"] == table.row_map(table.rows[1])["1지망_조직"]


def test_merge_defaults_to_base_when_unselected(client, sample_bytes, sample_round_id):
    base = _register(client, "취합_A.xlsx", sample_bytes, sample_round_id)
    other = _register(
        client, "취합_B.xlsx",
        _variant(sample_bytes, column="1지망_조직", new_value="제9기술원"),
        sample_round_id,
    )
    merged = client.post("/api/v1/versions/merge", json={
        "round_id": sample_round_id,
        "base_version_id": base["version_id"],
        "version_ids": [other["version_id"]],
        "selections": {},
        "actor": "pytest",
    }).json()["data"]
    assert merged["rows_from"][base["version_id"]] == merged["row_count"]


def test_merge_pulls_in_rows_missing_from_base(client, sample_bytes, sample_round_id):
    table = read_table(sample_bytes)
    trimmed = write_table(table, table.rows[:-3])

    base = _register(client, "취합_일부.xlsx", trimmed, sample_round_id)
    full = _register(client, "취합_전체.xlsx", sample_bytes, sample_round_id)

    merged = client.post("/api/v1/versions/merge", json={
        "round_id": sample_round_id,
        "base_version_id": base["version_id"],
        "version_ids": [full["version_id"]],
        "actor": "pytest",
    }).json()["data"]
    # 기준에 없던 3명은 다른 파일에서 끌어온다
    assert merged["row_count"] == len(table.ids())
    assert merged["rows_from"][full["version_id"]] == 3


def test_merge_rejects_team_file_as_base_when_master_exists(
    client, sample_bytes, sample_round_id
):
    """마스터가 있는 회차에서는 기준이 마스터여야 한다."""
    master = _register(client, "취합파일.xlsx", sample_bytes, sample_round_id)
    team = _register(client, "희망지원자_AI솔루션팀.xlsx", sample_bytes, sample_round_id)
    r = client.post("/api/v1/versions/merge", json={
        "round_id": sample_round_id,
        "base_version_id": team["version_id"],
        "version_ids": [master["version_id"]],
        "actor": "pytest",
    })
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_merge_uses_distribution_files_when_no_master(
    client, sample_bytes, sample_round_id
):
    """배포본만 올린 회차도 등록한 파일 그대로 최종 취합본이 된다."""
    table = read_table(sample_bytes)
    half = len(table.rows) // 2
    first = _register(client, "희망지원자_AI솔루션팀.xlsx",
                      write_table(table, table.rows[:half]), sample_round_id)
    second = _register(client, "희망지원자_전극기술팀.xlsx",
                       write_table(table, table.rows[half:]), sample_round_id)

    compared = client.post("/api/v1/versions/compare", json={
        "version_ids": [first["version_id"], second["version_id"]],
    }).json()["data"]
    assert compared["master_version_ids"] == []
    assert compared["mergeable_version_ids"] == [
        first["version_id"], second["version_id"]
    ]

    merged = client.post("/api/v1/versions/merge", json={
        "round_id": sample_round_id,
        "base_version_id": first["version_id"],
        "version_ids": [second["version_id"]],
        "actor": "pytest",
    }).json()["data"]
    # 두 배포본을 합치면 전원이 들어오고, 결과는 새 마스터로 등록된다
    assert merged["row_count"] == len(table.ids())
    active = client.get(f"/api/v1/versions/{sample_round_id}").json()["data"]
    assert active["version_id"] == merged["version_id"]


def test_compare_reports_master_as_mergeable(client, sample_bytes, sample_round_id):
    master = _register(client, "취합파일.xlsx", sample_bytes, sample_round_id)
    team = _register(client, "희망지원자_AI솔루션팀.xlsx", sample_bytes, sample_round_id)
    compared = client.post("/api/v1/versions/compare", json={
        "version_ids": [master["version_id"], team["version_id"]],
    }).json()["data"]
    # 마스터가 있으면 배포본은 병합 대상이 아니다
    assert compared["mergeable_version_ids"] == [master["version_id"]]


# ------------------------------------------------------------------ 담당팀 컬럼

def _merge_with_teams(client, sample_bytes, round_id, *, first=10, second=(5, 15)):
    """마스터 + 팀별 배포본 두 개를 올리고 병합한다 (겹치는 구간이 중복면접이 된다)."""
    table = read_table(sample_bytes)
    master = _register(client, "취합파일.xlsx", sample_bytes, round_id)
    _register(client, "희망지원자_AI솔루션팀.xlsx",
              write_table(table, table.rows[:first]), round_id)
    _register(client, "희망지원자_전극기술팀.xlsx",
              write_table(table, table.rows[second[0]:second[1]]), round_id)

    r = client.post("/api/v1/versions/merge", json={
        "round_id": round_id,
        "base_version_id": master["version_id"],
        "version_ids": [master["version_id"]],
        "actor": "pytest",
    })
    assert r.status_code == 201, r.text
    return table, r.json()["data"]


def _preview_teams(client, version_id, limit=500) -> dict[str, str]:
    preview = client.get(
        f"/api/v1/versions/by-id/{version_id}/preview", params={"limit": limit}
    ).json()["data"]
    assert "담당팀" in preview["columns"]
    return {row["지원자 번호"]: row["담당팀"] for row in preview["rows"]}


def test_merge_writes_team_column_from_distribution_files(
    client, sample_bytes, sample_round_id
):
    """팀 이름은 파일명에만 있다 — 병합이 그것을 취합본에 새겨야 뒤 단계가 읽는다."""
    table, merged = _merge_with_teams(client, sample_bytes, sample_round_id)
    teams = _preview_teams(client, merged["version_id"])
    ids = table.ids()

    assert teams[ids[0]] == "AI솔루션팀"          # 첫 파일에만 든 사람
    assert teams[ids[12]] == "전극기술팀"          # 둘째 파일에만 든 사람
    assert teams[ids[-1]] == ""                   # 아무 팀도 적어 내지 않은 사람
    assert merged["team_column"] == "담당팀"
    assert merged["teamed_count"] == 15
    assert len(merged["teamless"]) == len(ids) - 15


def test_merge_joins_two_teams_with_comma(client, sample_bytes, sample_round_id):
    """두 팀이 같이 보겠다고 적어 낸 사람은 한 칸에 쉼표로 남는다."""
    table, merged = _merge_with_teams(client, sample_bytes, sample_round_id)
    teams = _preview_teams(client, merged["version_id"])

    shared = table.ids()[5:10]
    assert all(teams[aid] == "AI솔루션팀, 전극기술팀" for aid in shared)
    assert merged["team_duplicate_count"] == len(shared)


def test_merge_overwrites_existing_team_column(client, sample_bytes, sample_round_id):
    """이미 담당팀이 든 취합본을 다시 기준으로 삼아도 컬럼이 늘지 않는다."""
    _table, first = _merge_with_teams(client, sample_bytes, sample_round_id)

    again = client.post("/api/v1/versions/merge", json={
        "round_id": sample_round_id,
        "base_version_id": first["version_id"],
        "version_ids": [first["version_id"]],
        "actor": "pytest",
    })
    assert again.status_code == 201, again.text
    second = again.json()["data"]

    columns = client.get(
        f"/api/v1/versions/by-id/{second['version_id']}/preview", params={"limit": 1}
    ).json()["data"]["columns"]
    assert columns.count("담당팀") == 1
    assert second["teamed_count"] == first["teamed_count"]


# ------------------------------------------------------------------ 회차 초기화

def test_reset_round_clears_history(client, sample_bytes, sample_round_id):
    """같은 회차를 다시 올릴 때 이전 이력이 남아 있으면 안 된다."""
    _register(client, "취합파일.xlsx", sample_bytes, sample_round_id)
    _register(client, "희망지원자_AI솔루션팀.xlsx", sample_bytes, sample_round_id)
    assert len(client.get(f"/api/v1/versions/{sample_round_id}/history").json()["data"]) == 2

    r = client.delete(f"/api/v1/versions/{sample_round_id}")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["deleted_versions"] == 2
    assert client.get(f"/api/v1/versions/{sample_round_id}/history").json()["data"] == []


def test_register_batch_reset_keeps_only_new_files(client, sample_bytes, sample_round_id):
    old = _register(client, "취합파일.xlsx", sample_bytes, sample_round_id)

    r = client.post(
        "/api/v1/versions/register-batch",
        files=[("files", ("취합파일.xlsx", io.BytesIO(sample_bytes), XLSX))],
        data={"round_id": sample_round_id, "actor": "pytest", "reset": "true"},
    )
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["cleared"]["deleted_versions"] == 1

    hist = client.get(f"/api/v1/versions/{sample_round_id}/history").json()["data"]
    assert [h["version_id"] for h in hist] == [data["registered"][0]["version_id"]]
    assert old["version_id"] not in {h["version_id"] for h in hist}
    # 새로 올린 것이 활성이고 이전 버전을 부모로 물지 않는다
    assert hist[0]["is_active"] is True
    assert hist[0]["parent_version"] is None


def test_reset_round_leaves_other_rounds_alone(client, sample_bytes, sample_round_id):
    _register(client, "취합파일.xlsx", sample_bytes, sample_round_id)
    _register(client, "취합파일.xlsx", sample_bytes, f"{sample_round_id}-other")

    client.delete(f"/api/v1/versions/{sample_round_id}")
    other = client.get(f"/api/v1/versions/{sample_round_id}-other/history").json()["data"]
    assert len(other) == 1
