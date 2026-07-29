"""팀 프로필 API 테스트."""


def test_list_profiles_seeded(client):
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 5
    names = {p["team_name"] for p in data}
    assert names == {
        "AI솔루션팀",
        "로봇응용기술팀",
        "미래혁신팀",
        "배터리기술팀",
        "전극기술팀",
    }
    robot = next(p for p in data if p["team_name"] == "로봇응용기술팀")
    assert robot["special_tags"] == ["타겟랩", "지도교수"]
    assert robot["target_headcount"] == 19


def test_get_profile(client):
    response = client.get("/api/v1/profiles/AI솔루션팀")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["primary_job"] == ["직무다"]
    assert data["org_allowed"] == ["제1기술원"]


def test_get_profile_not_found(client):
    response = client.get("/api/v1/profiles/없는팀")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_update_profile_changes_distribution(client):
    payload = {
        "primary_job": ["직무가"],
        "secondary_job": ["직무나"],
        "preferred_majors": ["푸른화학과"],
        "org_allowed": ["제1기술원", "제2사업부"],
        "grad_ratio_target": 0.4,
        "target_headcount": 10,
        "special_tags": ["타겟랩"],
    }
    response = client.put("/api/v1/profiles/AI솔루션팀", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["primary_job"] == ["직무가"]
    assert data["target_headcount"] == 10

    # 갱신된 정원이 새 배포안에 반영된다 (총 정원 88 → 82, 미배정 385명)
    created = client.post(
        "/api/v1/distribute/plan",
        json={"round_id": "R2026-Q3-01", "master_version_id": "vm_abc123"},
    ).json()["data"]
    assert created["team_counts"]["AI솔루션팀"] == 10
    assert created["total_applicants"] == 82
    assert len(created["unassigned"]) == 467 - 82


def test_update_profile_not_found(client):
    response = client.put(
        "/api/v1/profiles/없는팀",
        json={"target_headcount": 5},
    )
    assert response.status_code == 404


def test_update_profile_invalid_ratio(client):
    response = client.put(
        "/api/v1/profiles/AI솔루션팀",
        json={"target_headcount": 5, "grad_ratio_target": 1.5},
    )
    assert response.status_code == 422
