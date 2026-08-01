"""시간표 API 통합 테스트 — 명세 완료 판정 체크리스트"""
from __future__ import annotations

import time

import pytest

from app.infrastructure.contracts import DAYS, HOURS


def _generate(client, algorithm="v5", round_id="R2026-Q3-01", plan_id="plan-api"):
    return client.post(
        "/api/v1/schedules/generate",
        json={"round_id": round_id, "plan_id": plan_id, "algorithm": algorithm},
    )


# --------------------------------------------------------------------------
# 완료 판정 체크리스트
# --------------------------------------------------------------------------
def test_generate_responds_within_3_seconds(client):
    started = time.perf_counter()
    resp = _generate(client, "v5")
    elapsed = time.perf_counter() - started

    assert resp.status_code == 201
    assert elapsed < 3.0
    assert resp.json()["data"]["elapsed_ms"] < 3000


def test_v4_overall_at_least_85(client):
    data = _generate(client, "v4_hierarchical").json()["data"]
    assert data["rule_compliance"]["overall"] >= 85.0


def test_v5_coverage_90_and_overall_85(client):
    data = _generate(client, "v5").json()["data"]
    assert data["coverage_pct"] >= 90.0
    assert data["rule_compliance"]["overall"] >= 85.0


def test_lock_downgrade_returns_400(client, generated):
    sid = generated["schedule_id"]
    assert client.post(f"/api/v1/schedules/{sid}/lock", json={"lock_level": "CONFIRMED"}).status_code == 200

    resp = client.post(f"/api/v1/schedules/{sid}/lock", json={"lock_level": "DRAFT"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"
    assert resp.json()["data"] is None


def test_lock_skip_step_returns_400(client, generated):
    resp = client.post(
        f"/api/v1/schedules/{generated['schedule_id']}/lock", json={"lock_level": "LOCKED"}
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# 생성 응답 형태
# --------------------------------------------------------------------------
@pytest.mark.parametrize("algorithm", ["v1", "v4", "v5"])
def test_generate_response_shape(client, algorithm):
    resp = _generate(client, algorithm)
    body = resp.json()

    assert resp.status_code == 201
    assert body["error"] is None
    data = body["data"]
    for key in ("schedule_id", "total_assigned", "coverage_pct", "hard_violations", "rule_compliance"):
        assert key in data
    assert data["hard_violations"] == 0
    assert set(data["rule_compliance"]) == {
        "rule1_grad_balance",
        "rule2_team_conflict",
        "rule3_vertical_group",
        "rule4_first_slot",
        "overall",
    }


def test_generate_rejects_unknown_algorithm(client):
    resp = _generate(client, "v99")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


# --------------------------------------------------------------------------
# 부서 매칭 종속
# --------------------------------------------------------------------------
def test_generate_only_schedules_matched_applicants(client):
    """짝을 보내면 그 사람들만 시간표에 들어간다 — 나머지는 '면접 못 보는 사람'.

    화면 ②의 '면접 못 보는 사람'과 ③의 '시간표에 들어간 사람'이 같은 근거를
    보게 하려는 것이다. 예전에는 시간표가 짝을 무시하고 전원을 배정해서
    ②는 67명이 못 본다고 하는데 ③은 83/83 이 들어갔다고 했다.
    """
    full = _generate(client, "v5", round_id="R2026-Q3-PAIR-A").json()["data"]
    every = client.get(f"/api/v1/schedules/{full['schedule_id']}").json()["data"]
    pairs = {
        a["applicant_id"]: a["interviewer_id"]
        for a in every["assignments"][:12]
    }

    resp = client.post(
        "/api/v1/schedules/generate",
        json={
            "round_id": "R2026-Q3-PAIR-B", "plan_id": "plan-pairs",
            "algorithm": "v5", "pairs": pairs,
        },
    )
    assert resp.status_code == 201
    data = resp.json()["data"]

    # 배정된 사람 수는 짝 수를 넘지 않고, 모수는 회차 전체 그대로다
    assert data["total_assigned"] <= len(pairs)
    assert data["total_applicants"] == full["total_applicants"]
    assert data["hard_violations"] == 0

    got = client.get(f"/api/v1/schedules/{data['schedule_id']}").json()["data"]
    for a in got["assignments"]:
        assert a["applicant_id"] in pairs
        assert a["interviewer_id"] == pairs[a["applicant_id"]]


def test_generate_refuses_when_nothing_is_matched(client):
    """부서가 아직 아무도 짝짓지 않았으면 시간표를 만들지 않는다"""
    resp = client.post(
        "/api/v1/schedules/generate",
        json={
            "round_id": "R2026-Q3-01", "plan_id": "plan-empty-pairs",
            "algorithm": "v5", "pairs": {},
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_FAILED"


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------
def test_get_schedule_returns_assignments(client, generated):
    resp = client.get(f"/api/v1/schedules/{generated['schedule_id']}")
    data = resp.json()["data"]

    assert resp.status_code == 200
    assert len(data["assignments"]) == generated["total_assigned"]
    first = data["assignments"][0]
    assert first["day"] in DAYS and first["hour"] in HOURS
    assert first["lock_level"] == "DRAFT"


def test_timing_survives_generate_and_revalidation(client):
    """면접 진행 조건은 시간표에 붙어 다녀야 한다.

    인사팀이 팀별 명단 나누기에서 정한 조건이 칸의 실제 시각을 정하고, 규칙4의
    '오후 첫 타임' 도 그에 따라 움직인다. 만들 때 쓴 조건을 안 남기면 나중에
    다시 검증할 때 기본 조건(30분 · 5분)으로 재게 되어, 같은 시간표인데 점수가
    달라지고 재편성(05)도 다른 칸을 지키려 든다.
    """
    long_day = {"start": "09:00", "minutes": 60, "rest": 10}
    created = client.post(
        "/api/v1/schedules/generate",
        json={"round_id": "R2026-Q3-TIMING", "plan_id": "plan-timing", "algorithm": "v5",
              "constraints": {"timing": long_day}},
    ).json()["data"]
    sid = created["schedule_id"]

    assert client.get(f"/api/v1/schedules/{sid}").json()["data"]["timing"] == long_day

    detail = client.get(f"/api/v1/schedules/{sid}/rules").json()["data"]
    assert detail["rule4_first_slot"]["detail"]["first_slots"] == [HOURS[0], HOURS[3]]
    assert detail["rule4_first_slot"]["score"] == created["rule_compliance"]["rule4_first_slot"]

    # 다시 검증해도 같은 조건으로 재므로 점수가 흔들리지 않는다
    assert client.post(f"/api/v1/schedules/{sid}/validate").status_code == 200
    after = client.get(f"/api/v1/schedules/{sid}/rules").json()["data"]
    assert after["rule4_first_slot"]["detail"]["first_slots"] == [HOURS[0], HOURS[3]]
    assert after["rule4_first_slot"]["score"] == created["rule_compliance"]["rule4_first_slot"]


def test_grad_target_survives_generate_and_revalidation(client):
    """규칙1의 목표 비율도 시간표에 붙어 다녀야 한다.

    인사가 '하루 절반은 석박사' 라고 못 박았으면 나중에 다시 재도 그 잣대여야
    한다. 안 남기면 검증할 때는 명단의 실제 비율로 재게 되어, 같은 시간표인데
    점수가 달라진다. 화면이 '고쳐야 한다' 와 '괜찮다' 를 번갈아 말하게 된다.
    """
    created = client.post(
        "/api/v1/schedules/generate",
        json={"round_id": "R2026-Q3-GRAD", "plan_id": "plan-grad", "algorithm": "v5",
              "constraints": {"grad_ratio_target": 0.5, "grad_ratio_tolerance": 0.02}},
    ).json()["data"]
    sid = created["schedule_id"]
    pinned = created["rule_compliance"]["rule1_grad_balance"]

    live = client.get(f"/api/v1/schedules/{sid}/rules?recompute=true").json()["data"]
    assert live["rule1_grad_balance"]["detail"]["target"] == 0.5
    assert live["rule1_grad_balance"]["detail"]["target_source"] == "지정"
    assert live["rule1_grad_balance"]["score"] == pinned

    # 검증도 같은 잣대로 — 벌점이 그 점수 그대로 반영돼야 한다
    validated = client.post(f"/api/v1/schedules/{sid}/validate").json()["data"]
    expected = sum(
        (100.0 - created["rule_compliance"][key]) * 0.10
        for key in ("rule1_grad_balance", "rule3_vertical_group", "rule4_first_slot")
    )
    assert validated["soft_penalty"] == round(expected, 2)

    after = client.get(f"/api/v1/schedules/{sid}/rules").json()["data"]
    assert after["rule1_grad_balance"]["score"] == pinned


def test_grad_target_defaults_to_the_roster_ratio(client):
    """안 못 박으면 명단의 실제 비율로 잰다 — 재는 것은 요일 분산이다."""
    created = _generate(client, "v5", round_id="R2026-Q3-ROSTER").json()["data"]
    sid = created["schedule_id"]

    detail = client.get(f"/api/v1/schedules/{sid}/rules").json()["data"]
    detail = detail["rule1_grad_balance"]["detail"]
    assert detail["target_source"] == "명단"
    # 목표가 명단에서 왔으니 하루하루의 비율은 그 값 언저리에 모여 있다
    assert min(detail["ratios"].values()) <= detail["target"] <= max(detail["ratios"].values())


def test_list_round_schedules(client, generated):
    """화면이 '어느 시간표를 볼까요' 를 감사 로그가 아니라 여기서 받아야 한다."""
    other = _generate(client, "v4", round_id="R2026-Q3-02").json()["data"]

    rows = client.get("/api/v1/schedules/rounds/R2026-Q3-01").json()["data"]
    ids = [r["schedule_id"] for r in rows]
    assert generated["schedule_id"] in ids
    assert other["schedule_id"] not in ids          # 다른 회차는 섞이지 않는다
    row = next(r for r in rows if r["schedule_id"] == generated["schedule_id"])
    assert row["round_id"] == "R2026-Q3-01"
    assert row["total_assigned"] == generated["total_assigned"]
    assert row["generated_at"]
    # 최신순 — 화면이 맨 위를 기본값으로 쓴다
    assert [r["generated_at"] for r in rows] == sorted(
        (r["generated_at"] for r in rows), reverse=True
    )


def test_list_round_schedules_empty(client):
    resp = client.get("/api/v1/schedules/rounds/R-없는회차")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_get_schedule_not_found(client):
    resp = client.get("/api/v1/schedules/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_heatmap(client, generated):
    data = client.get(f"/api/v1/schedules/{generated['schedule_id']}/heatmap").json()["data"]

    assert data["days"] == DAYS
    assert data["hours"] == HOURS
    assert data["total"] == generated["total_assigned"]
    assert sum(sum(row.values()) for row in data["grid"].values()) == generated["total_assigned"]
    # 규칙2(HARD): 어떤 슬롯도 팀 수(5)를 넘지 않는다
    assert data["peak"] <= 5


def test_by_team(client, generated):
    data = client.get(f"/api/v1/schedules/{generated['schedule_id']}/by-team").json()["data"]

    assert sum(data["counts"].values()) == generated["total_assigned"]
    for team, rows in data["teams"].items():
        assert all(r["day"] in DAYS for r in rows)
        # 팀 내 (날, 시간) 조합은 유일해야 한다 — 규칙2
        slots = [(r["day"], r["hour"]) for r in rows]
        assert len(slots) == len(set(slots)), team


def test_heatmap_not_found(client):
    assert client.get("/api/v1/schedules/nope/heatmap").status_code == 404
    assert client.get("/api/v1/schedules/nope/by-team").status_code == 404


# --------------------------------------------------------------------------
# 규칙 조회 · 검증
# --------------------------------------------------------------------------
def test_rules_endpoint_returns_score_and_detail(client, generated):
    data = client.get(f"/api/v1/schedules/{generated['schedule_id']}/rules").json()["data"]

    assert data["rule1_grad_balance"]["score"] == generated["rule_compliance"]["rule1_grad_balance"]
    assert "ratios" in data["rule1_grad_balance"]["detail"]
    assert data["overall"]["score"] == generated["rule_compliance"]["overall"]


def test_rules_recompute_matches_stored(client, generated):
    stored = client.get(f"/api/v1/schedules/{generated['schedule_id']}/rules").json()["data"]
    live = client.get(
        f"/api/v1/schedules/{generated['schedule_id']}/rules?recompute=true"
    ).json()["data"]

    assert stored["rule2_team_conflict"]["score"] == live["rule2_team_conflict"]["score"]


def test_rules_not_found(client):
    assert client.get("/api/v1/schedules/nope/rules").status_code == 404


def test_validate_reports_zero_hard_violations(client, generated):
    data = client.post(f"/api/v1/schedules/{generated['schedule_id']}/validate").json()["data"]

    assert data["hard_violations"] == []
    assert data["soft_penalty"] >= 0


def test_validate_not_found(client):
    assert client.post("/api/v1/schedules/nope/validate").status_code == 404


# --------------------------------------------------------------------------
# 락
# --------------------------------------------------------------------------
def test_full_lock_lifecycle(client):
    sid = _generate(client, "v5", plan_id="lock-lifecycle").json()["data"]["schedule_id"]

    step1 = client.post(f"/api/v1/schedules/{sid}/lock", json={"lock_level": "CONFIRMED"})
    assert step1.status_code == 200
    assert step1.json()["data"]["status"] == "confirmed"

    step2 = client.post(f"/api/v1/schedules/{sid}/lock", json={"lock_level": "LOCKED"})
    assert step2.status_code == 200
    assert step2.json()["data"]["status"] == "locked"

    assert client.post(f"/api/v1/schedules/{sid}/lock", json={"lock_level": "LOCKED"}).status_code == 400


def test_partial_lock_keeps_schedule_in_draft(client):
    data = _generate(client, "v5", plan_id="lock-partial").json()["data"]
    sid = data["schedule_id"]
    assignments = client.get(f"/api/v1/schedules/{sid}").json()["data"]["assignments"]
    target = [assignments[0]["applicant_id"], assignments[1]["applicant_id"]]

    resp = client.post(
        f"/api/v1/schedules/{sid}/lock",
        json={"lock_level": "CONFIRMED", "applicant_ids": target},
    )
    body = resp.json()["data"]

    assert resp.status_code == 200
    assert body["changed"] == 2
    assert body["status"] == "draft"  # 나머지가 DRAFT이므로 스케줄 전체는 draft


def test_lock_unknown_applicant_returns_400(client, generated):
    resp = client.post(
        f"/api/v1/schedules/{generated['schedule_id']}/lock",
        json={"lock_level": "CONFIRMED", "applicant_ids": ["9999999"]},
    )
    assert resp.status_code == 400


def test_lock_invalid_level_returns_400(client, generated):
    resp = client.post(
        f"/api/v1/schedules/{generated['schedule_id']}/lock", json={"lock_level": "ARCHIVED"}
    )
    assert resp.status_code == 400


def test_lock_not_found(client):
    assert (
        client.post("/api/v1/schedules/nope/lock", json={"lock_level": "CONFIRMED"}).status_code
        == 404
    )
