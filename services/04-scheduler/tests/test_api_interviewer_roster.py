"""면접관명단 업로드 + 회차별 선별 (콘솔 3번 메뉴가 쓰는 경로).

합성 샘플(tools/fixtures/면접관명단_sample.xlsx)만 쓴다 — 실제 명단은
사번·이메일이 들어 있어 저장소에 올리지 않는다.
"""
import io
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.domain.interviewer import Interviewer
from app.domain.round_interviewer import RoundInterviewer
from app.infrastructure.db import SessionLocal
from app.services import interviewer_roster, schedule_service

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SAMPLE = (
    Path(__file__).resolve().parents[3] / "tools" / "fixtures" / "면접관명단_sample.xlsx"
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
def clean_roster(db):
    """마스터/선별을 비운 상태에서 시작하고, 끝나면 부팅 시드를 되돌린다.

    DB 파일이 테스트 세션 전체에서 공유되므로 여기서 지운 목 면접관을
    복구하지 않으면 뒤따르는 스케줄 테스트가 빈 명단으로 돌게 된다.
    """
    def wipe():
        db.query(RoundInterviewer).delete()
        db.query(Interviewer).delete()
        db.commit()

    wipe()
    yield
    wipe()
    schedule_service.seed_interviewers(db)


@pytest.fixture
def roster_bytes():
    if not SAMPLE.is_file():
        pytest.skip(f"합성 샘플 없음: {SAMPLE} — tools/make_interviewer_sample.py 실행")
    return SAMPLE.read_bytes()


def _sheet_bytes(rows: list[list]) -> bytes:
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _upload(client, data, name="면접관명단.xlsx"):
    return client.post(
        "/api/v1/interviewers/import",
        files={"file": (name, io.BytesIO(data), XLSX)},
        data={"actor": "pytest"},
    )


# ------------------------------------------------------------------ 업로드

def test_import_creates_master_rows(client, roster_bytes):
    r = _upload(client, roster_bytes)
    assert r.status_code == 201, r.text
    data = r.json()["data"]

    assert data["parsed"] == 20  # 5팀 × 4명
    assert data["created"] == 20
    assert data["updated"] == 0
    assert len(data["teams"]) == 5

    listed = client.get("/api/v1/interviewers").json()["data"]
    assert len(listed) == 20
    leader = next(i for i in listed if i["interviewer_id"] == "IV101")
    assert leader["priority"] == 1
    assert leader["max_daily"] == 4
    assert leader["email"] == "iv101@example.com"


def test_import_is_idempotent(client, roster_bytes):
    _upload(client, roster_bytes)
    data = _upload(client, roster_bytes).json()["data"]
    assert data["created"] == 0
    assert data["updated"] == 20
    assert len(client.get("/api/v1/interviewers").json()["data"]) == 20


def test_import_keeps_existing_availability(client, db, roster_bytes):
    """회신으로 채워진 가용성을 재업로드가 지우면 안 된다."""
    _upload(client, roster_bytes)
    row = db.get(Interviewer, "IV101")
    row.availability = {"월": ["10시"]}
    db.commit()

    _upload(client, roster_bytes)
    db.expire_all()
    assert db.get(Interviewer, "IV101").availability == {"월": ["10시"]}


def test_import_accepts_alias_columns(client):
    data = _sheet_bytes([
        ["사원번호", "이름", "팀", "메일", "하루최대", "우선순위"],
        ["A1", "홍길동", "AI솔루션팀", "a1@example.com", "5", "1"],
    ])
    result = _upload(client, data).json()["data"]
    assert result["parsed"] == 1
    assert result["interviewers"][0] == {
        "interviewer_id": "A1", "name": "홍길동", "team": "AI솔루션팀",
        "email": "a1@example.com", "max_daily": 5, "priority": 1,
    }


def test_import_uses_defaults_for_blank_numbers(client):
    data = _sheet_bytes([["사번", "성명", "소속팀"], ["A1", "홍길동", "AI솔루션팀"]])
    parsed = _upload(client, data).json()["data"]["interviewers"][0]
    assert parsed["max_daily"] == 6
    assert parsed["priority"] == 2


def test_import_rejects_file_without_headers(client):
    data = _sheet_bytes([["아무말", "대잔치"], ["1", "2"]])
    r = _upload(client, data)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


# ------------------------------------------------------------------ 회차 선별

def test_select_for_round(client, roster_bytes, sample_round_id):
    _upload(client, roster_bytes)
    picked = ["IV101", "IV102", "IV201"]

    r = client.put(
        f"/api/v1/interviewers/rounds/{sample_round_id}",
        json={"interviewer_ids": picked, "actor": "pytest"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["selected"] == 3

    listed = client.get(f"/api/v1/interviewers/rounds/{sample_round_id}").json()["data"]
    assert [i["interviewer_id"] for i in listed] == picked


def test_select_replaces_previous_selection(client, roster_bytes, sample_round_id):
    _upload(client, roster_bytes)
    client.put(f"/api/v1/interviewers/rounds/{sample_round_id}",
               json={"interviewer_ids": ["IV101", "IV102"]})
    client.put(f"/api/v1/interviewers/rounds/{sample_round_id}",
               json={"interviewer_ids": ["IV301"]})
    listed = client.get(f"/api/v1/interviewers/rounds/{sample_round_id}").json()["data"]
    assert [i["interviewer_id"] for i in listed] == ["IV301"]


def test_select_rejects_unknown_interviewer(client, roster_bytes, sample_round_id):
    _upload(client, roster_bytes)
    r = client.put(f"/api/v1/interviewers/rounds/{sample_round_id}",
                   json={"interviewer_ids": ["IV101", "없는사번"]})
    assert r.status_code == 400
    assert "없는사번" in r.json()["error"]["message"]


def test_unselected_round_is_empty(client, roster_bytes):
    _upload(client, roster_bytes)
    assert client.get("/api/v1/interviewers/rounds/R-없음").json()["data"] == []


# --------------------------------------------------- 선별 결과가 배치에 반영되는가

def test_schedule_uses_only_selected_interviewers(client, db, roster_bytes, sample_round_id):
    _upload(client, roster_bytes)
    picked = ["IV101", "IV102", "IV201", "IV202"]
    interviewer_roster.select_for_round(db, sample_round_id, picked)

    loaded = schedule_service.load_interviewers(db, sample_round_id)
    assert sorted(i.interviewer_id for i in loaded) == sorted(picked)
    # 03 회신이 없으면 제약 없음(전 요일·전 시간대)으로 둔다
    assert all(iv.availability for iv in loaded)


def test_schedule_falls_back_to_master_without_selection(client, db, roster_bytes):
    _upload(client, roster_bytes)
    loaded = schedule_service.load_interviewers(db, "R-선별없음")
    assert len(loaded) == 20
