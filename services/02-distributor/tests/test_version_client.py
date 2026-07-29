"""VersionClient (목/원격) 테스트."""
import httpx
import pytest

from app.infrastructure.version_client import (
    MasterNotFound,
    VersionClient,
    generate_mock_master,
)


def test_mock_master_is_deterministic():
    first = generate_mock_master(seed=42)
    second = generate_mock_master(seed=42)
    assert [a.applicant_id for a in first] == [a.applicant_id for a in second]


def test_different_seed_differs():
    assert [a.applicant_id for a in generate_mock_master(seed=1)] != [
        a.applicant_id for a in generate_mock_master(seed=2)
    ]


def test_mock_mode_prefers_real_master_file(master_applicants):
    """MASTER_XLSX가 있으면 합성 데이터가 아니라 실제 취합파일을 읽는다."""
    client = VersionClient(use_mock=True)
    fetched = client.fetch_master("vm_abc123")
    assert [a.applicant_id for a in fetched] == [
        a.applicant_id for a in master_applicants
    ]
    # 실파일 모드에서는 version_id와 무관하게 같은 스냅샷을 돌려준다
    assert [a.applicant_id for a in client.fetch_master("vm_zzz999")] == [
        a.applicant_id for a in fetched
    ]


def test_synthetic_fallback_when_no_master_file(synthetic_master):
    """취합파일이 없으면 version_id별로 결정적인 합성 데이터로 폴백한다."""
    client = VersionClient(use_mock=True)
    first = client.fetch_master("vm_abc123")
    second = client.fetch_master("vm_abc123")
    other = client.fetch_master("vm_zzz999")
    assert [a.applicant_id for a in first] == [a.applicant_id for a in second]
    assert [a.applicant_id for a in first] != [a.applicant_id for a in other]
    assert len(first) == 467


@pytest.mark.uses_version_manager
def test_remote_mode_parses_registered_file(monkeypatch, master_xlsx_bytes):
    """01이 내려준 원본 엑셀을 그대로 파싱한다 — 로컬 파일이 아니라."""
    seen = {}

    def fake_get(url, timeout=None, **kwargs):
        seen["url"] = url
        return httpx.Response(
            200, content=master_xlsx_bytes, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    applicants = VersionClient(use_mock=False).fetch_master("vm_1")
    assert applicants, "01이 준 파일에서 지원자를 못 뽑았다"
    assert "/api/v1/versions/by-id/vm_1/file" in seen["url"]


@pytest.mark.uses_version_manager
def test_remote_mode_404(monkeypatch):
    def fake_get(url, timeout=None, **kwargs):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(MasterNotFound):
        VersionClient(use_mock=False).fetch_master("vm_missing")


@pytest.mark.uses_version_manager
def test_remote_transport_error(monkeypatch):
    def fake_get(url, timeout=None, **kwargs):
        raise httpx.ConnectError("연결 실패")

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(MasterNotFound):
        VersionClient(use_mock=False).fetch_master("vm_x")


@pytest.mark.uses_version_manager
def test_remote_failure_falls_back_when_mock_allowed(monkeypatch, synthetic_master):
    """USE_MOCK=true 면 01이 죽어 있어도 배포가 멈추지는 않는다."""
    def fake_get(url, timeout=None, **kwargs):
        raise httpx.ConnectError("연결 실패")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert len(VersionClient(use_mock=True).fetch_master("vm_x")) == 467


def test_plan_creation_surfaces_master_not_found(client, monkeypatch):
    from app.infrastructure import version_client as vc

    def boom(self, master_version_id):
        raise MasterNotFound("없음")

    monkeypatch.setattr(vc.VersionClient, "fetch_master", boom)
    response = client.post(
        "/api/v1/distribute/plan",
        json={"round_id": "R2026-Q3-01", "master_version_id": "vm_missing"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
