"""실시간 KPI 대시보드 API · 조직별 협업 온도계"""
from __future__ import annotations

import pytest

from app.api.dashboard import build_kpi, build_organizations
from app.domain.kpi import temperature_of
from app.services.demo_data import DEMO_ROUND

KPI_KEYS = {"총_대상자", "회신_완료", "회신_대기", "배치_완료율", "규칙_준수율", "위험도", "실행_시간_초"}


def test_kpi_endpoint_returns_spec_shape(seeded_client):
    response = seeded_client.get(f"/api/v1/dashboard/kpi?round_id={DEMO_ROUND}")

    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert set(body["data"]) == KPI_KEYS


def test_kpi_values_match_demo_stream(seeded):
    kpi = build_kpi(seeded, DEMO_ROUND)

    assert kpi["총_대상자"] == 88
    assert kpi["회신_완료"] == 46
    assert kpi["회신_대기"] == 4
    assert kpi["배치_완료율"] == pytest.approx(88.6, abs=0.1)
    assert kpi["규칙_준수율"] == pytest.approx(90.5)
    assert kpi["실행_시간_초"] == pytest.approx(3.2)
    assert kpi["위험도"] in {"Low", "Medium", "High"}


def test_kpi_for_unknown_round_returns_zeros_not_404(client):
    """대시보드가 렌더 도중 깨지지 않도록 빈 회차도 200으로 응답한다"""
    response = client.get("/api/v1/dashboard/kpi?round_id=R-NONE")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["총_대상자"] == 0
    assert data["회신_완료"] == 0
    assert data["위험도"] == "Low"


def test_kpi_requires_round_id(client):
    response = client.get("/api/v1/dashboard/kpi")

    assert response.status_code == 422
    body = response.json()
    assert body["data"] is None
    assert body["error"]["code"] == "VALIDATION_FAILED"


def test_organizations_expose_collaboration_thermometer(seeded):
    orgs = {row["org"]: row for row in build_organizations(seeded, DEMO_ROUND)}

    assert set(orgs) == {"제1기술원", "제2사업부", "제3기술원", "제4연구소"}

    fast = orgs["제1기술원"]
    assert fast["avg_response_h"] == pytest.approx(6.0)
    assert fast["completion"] == pytest.approx(95.0)
    assert fast["temperature"] == "cool"
    assert fast["predicted_slow"] is False

    slow = orgs["제3기술원"]
    assert slow["avg_response_h"] == pytest.approx(52.0)
    assert slow["completion"] == pytest.approx(62.5)
    assert slow["temperature"] == "hot"
    assert slow["predicted_slow"] is True


def test_organizations_endpoint(seeded_client):
    response = seeded_client.get(
        f"/api/v1/dashboard/organizations?round_id={DEMO_ROUND}"
    )

    assert response.status_code == 200
    rows = response.json()["data"]
    assert len(rows) == 4
    assert set(rows[0]) == {
        "org",
        "avg_response_h",
        "completion",
        "temperature",
        "predicted_slow",
    }


@pytest.mark.parametrize(
    "hours, expected",
    [(None, "cool"), (0.0, "cool"), (6.0, "cool"), (12.0, "cool"),
     (12.1, "warm"), (40.0, "warm"), (40.1, "hot"), (52.0, "hot")],
)
def test_temperature_thresholds(hours, expected):
    assert temperature_of(hours) == expected


def test_risks_endpoint_returns_signals(seeded_client):
    response = seeded_client.get(f"/api/v1/dashboard/risks?round_id={DEMO_ROUND}")

    assert response.status_code == 200
    signals = response.json()["data"]
    assert signals, "데모 회차에서 위험 신호가 하나 이상 감지되어야 한다"
    assert all({"type", "severity"} <= set(s) for s in signals)
    # 심각도 높은 순 정렬
    order = {"high": 0, "medium": 1, "low": 2}
    weights = [order[s["severity"]] for s in signals]
    assert weights == sorted(weights)


def test_risk_signal_omits_irrelevant_fields(seeded_client):
    """신호 종류마다 노출 필드가 다르다 (team / count / interviewer)"""
    signals = seeded_client.get(
        f"/api/v1/dashboard/risks?round_id={DEMO_ROUND}"
    ).json()["data"]
    by_type = {s["type"]: s for s in signals}

    assert "count" not in by_type["담당자_변경"]
    assert "team" not in by_type["노쇼_예측"]
    assert "interviewer" in by_type["면접관_피로도"]
