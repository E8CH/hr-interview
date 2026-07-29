"""API E2E 테스트 — 실제 6개 엑셀로 전체 시나리오."""
import io

import pytest

from app.infrastructure.event_bus import get_event_bus


def _upload(client, name, data, round_id, kind, actor, team_name=None):
    files = {"file": (name, io.BytesIO(data),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    form = {"round_id": round_id, "kind": kind, "actor": actor}
    if team_name:
        form["team_name"] = team_name
    return client.post("/api/v1/versions/register", files=files, data=form)


@pytest.fixture
def bus():
    b = get_event_bus()
    b.published.clear()
    return b


def test_register_master(client, master_bytes, sample_round_id, bus):
    r = _upload(client, "master.xlsx", master_bytes, sample_round_id, "master", "HR김민지")
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["applicant_count"] == 467
    assert len(data["fingerprint"]) == 16
    # MASTER_REGISTERED 이벤트 발행 확인
    assert any(e["event_type"] == "MASTER_REGISTERED" for e in bus.published)


def test_register_team_requires_team_name(client, team_files, sample_round_id):
    team, data = next(iter(team_files.items()))
    r = _upload(client, "t.xlsx", data, sample_round_id, "team_distribution", "HR김민지")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_get_latest(client, master_bytes, sample_round_id):
    _upload(client, "master.xlsx", master_bytes, sample_round_id, "master", "HR김민지")
    r = client.get(f"/api/v1/versions/{sample_round_id}", params={"kind": "master"})
    assert r.status_code == 200
    assert r.json()["data"]["actor"] == "HR김민지"


def test_get_latest_not_found(client, sample_round_id):
    r = client.get(f"/api/v1/versions/{sample_round_id}", params={"kind": "master"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_full_scenario_verify(client, master_bytes, team_files, sample_round_id, bus):
    """6개 파일 등록 → 무결성 검증 → 중복5·미배포384 감지 + INTEGRITY_VIOLATED 발행."""
    _upload(client, "master.xlsx", master_bytes, sample_round_id, "master", "HR김민지")
    for team, data in team_files.items():
        r = _upload(client, f"{team}.xlsx", data, sample_round_id,
                    "team_distribution", "HR김민지", team_name=team)
        assert r.status_code == 201

    r = client.post(f"/api/v1/versions/verify/{sample_round_id}")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "ISSUES_FOUND"
    assert d["master_count"] == 467
    assert d["undistributed_count"] == 384
    assert d["duplicate_count"] == 5

    assert any(e["event_type"] == "DISTRIBUTION_REGISTERED" for e in bus.published)
    assert any(e["event_type"] == "INTEGRITY_VIOLATED" for e in bus.published)


def test_verify_no_master(client, sample_round_id):
    r = client.post(f"/api/v1/versions/verify/{sample_round_id}")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "NO_MASTER"


def test_history(client, master_bytes, team_files, sample_round_id):
    _upload(client, "master.xlsx", master_bytes, sample_round_id, "master", "HR김민지")
    team, data = next(iter(team_files.items()))
    _upload(client, "t.xlsx", data, sample_round_id, "team_distribution", "HR김민지", team_name=team)
    r = client.get(f"/api/v1/versions/{sample_round_id}/history")
    assert r.status_code == 200
    hist = r.json()["data"]
    assert len(hist) == 2
    assert all("version_id" in h for h in hist)


def test_reregister_deactivates_previous(client, master_bytes, sample_round_id):
    r1 = _upload(client, "m1.xlsx", master_bytes, sample_round_id, "master", "HR김민지")
    v1 = r1.json()["data"]["version_id"]
    # 다른 바이트로 재등록
    r2 = _upload(client, "m2.xlsx", master_bytes + b"x", sample_round_id, "master", "HR박준")
    v2 = r2.json()["data"]["version_id"]
    assert v1 != v2

    hist = client.get(f"/api/v1/versions/{sample_round_id}/history").json()["data"]
    active = [h for h in hist if h["is_active"]]
    assert len(active) == 1
    assert active[0]["version_id"] == v2
    assert active[0]["parent_version"] == v1


def test_rollback(client, master_bytes, sample_round_id):
    r1 = _upload(client, "m1.xlsx", master_bytes, sample_round_id, "master", "HR김민지")
    v1 = r1.json()["data"]["version_id"]
    _upload(client, "m2.xlsx", master_bytes + b"x", sample_round_id, "master", "HR박준")

    r = client.post("/api/v1/versions/rollback", json={"version_id": v1})
    assert r.status_code == 200
    assert r.json()["data"]["restored_version_id"] == v1

    # 롤백 후 v1이 active
    latest = client.get(f"/api/v1/versions/{sample_round_id}",
                        params={"kind": "master"}).json()["data"]
    assert latest["version_id"] == v1


def test_rollback_not_found(client):
    r = client.post("/api/v1/versions/rollback", json={"version_id": "vm_nope"})
    assert r.status_code == 404


def test_diff(client, master_bytes, team_files, sample_round_id):
    # 마스터(467) vs 한 팀(16) diff
    rm = _upload(client, "master.xlsx", master_bytes, sample_round_id, "master", "HR김민지")
    vm = rm.json()["data"]["version_id"]
    team, data = next(iter(team_files.items()))
    rt = _upload(client, "t.xlsx", data, sample_round_id, "team_distribution", "HR김민지", team_name=team)
    vt = rt.json()["data"]["version_id"]

    r = client.get("/api/v1/versions/diff", params={"from": vt, "to": vm})
    assert r.status_code == 200
    d = r.json()["data"]
    # 팀→마스터: 팀에 없던 마스터 지원자가 added
    assert len(d["added_ids"]) > 0
    assert d["unchanged_count"] >= 0
