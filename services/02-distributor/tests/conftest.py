"""공통 pytest fixture — 테스트마다 격리된 SQLite 파일 + 인메모리 이벤트 버스."""
import os
import sys
import tempfile
from pathlib import Path

# app import 이전에 환경변수를 고정해야 엔진/스토리지가 테스트 경로를 바라본다.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="dist-test-"))
os.environ.setdefault("USE_MOCK", "true")
os.environ["REDIS_URL"] = "fakeredis://"
os.environ["STORAGE_DIR"] = str(_TMP_ROOT / "storage")
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_ROOT / 'bootstrap.sqlite').as_posix()}"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.domain.profile import seed_profiles  # noqa: E402
from app.infrastructure import event_bus  # noqa: E402
from app.infrastructure.db import init_db, new_session, reset_engines  # noqa: E402
from app.infrastructure.master_excel import load_master_excel, load_team_roster  # noqa: E402

SERVICE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MASTER_XLSX = SERVICE_ROOT / "data" / "취합파일.xlsx"
TEAM_NAMES = ["AI솔루션팀", "로봇응용기술팀", "미래혁신팀", "배터리기술팀", "전극기술팀"]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "dist.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    reset_engines()
    init_db()
    yield path
    reset_engines()


@pytest.fixture
def session(db_path):
    with new_session() as sess:
        yield sess


@pytest.fixture(autouse=True)
def fresh_bus():
    event_bus.reset_bus()
    yield
    event_bus.reset_bus()


@pytest.fixture
def bus():
    return event_bus.get_bus()


@pytest.fixture
def client(db_path):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def profiles():
    return seed_profiles()


@pytest.fixture(scope="session")
def master_applicants():
    """실제 취합파일 467명."""
    return load_master_excel(MASTER_XLSX)


@pytest.fixture(scope="session")
def master_xlsx_bytes():
    """01이 내려줄 원본 엑셀 바이트 (개인정보 없는 합성 샘플)."""
    sample = Path(__file__).resolve().parents[3] / "tools" / "fixtures" / "master_sample.xlsx"
    if not sample.is_file():
        pytest.skip(f"샘플 마스터 없음: {sample}")
    return sample.read_bytes()


@pytest.fixture(scope="session")
def team_truth():
    """희망지원자_{팀}.xlsx — 실제 배포 결과(정답지) 팀별 지원자 번호 집합."""
    return {
        team: set(load_team_roster(FIXTURES / f"희망지원자_{team}.xlsx"))
        for team in TEAM_NAMES
    }


@pytest.fixture(autouse=True)
def no_version_manager(monkeypatch, request):
    """01(version-manager)을 부를 수 없는 상태로 고정한다.

    fetch_master 는 이제 01을 먼저 호출하므로, 폴백 경로를 검증하는
    테스트가 실제로 떠 있는 로컬 서비스에 붙어버리면 결과가 달라진다.
    01 호출 자체를 검증하는 테스트는 `uses_version_manager` 마커로 제외한다.
    """
    if request.node.get_closest_marker("uses_version_manager"):
        return
    import httpx

    def refuse(url, timeout=None, **kwargs):
        raise httpx.ConnectError("version-manager 미기동 (테스트 고정)")

    monkeypatch.setattr(httpx, "get", refuse)


@pytest.fixture
def synthetic_master(monkeypatch):
    """합성 데이터 폴백 경로를 강제한다 (MASTER_XLSX 비활성화)."""
    monkeypatch.setenv("MASTER_XLSX", "")


@pytest.fixture
def sample_round_id():
    return "R2026-Q3-01"
