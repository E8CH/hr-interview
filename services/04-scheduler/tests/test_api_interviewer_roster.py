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
from app.infrastructure.contracts import (
    BAND_ALL,
    BAND_BACK,
    BAND_FRONT,
    HOURS,
    band_hours,
    band_of,
    normalize_availability,
)
from app.infrastructure.db import SessionLocal
from app.services import interviewer_roster, schedule_service

# 앞타임(~14시)과 뒤타임(12시~)은 겹친다 — 같은 칸이 양쪽에 들어갈 수 있다.
# 기본 진행 조건(09시 시작 · 30분 · 쉬는 시간 5분)에서는 마지막 칸이 13시 35분에
# 끝나므로 앞타임이 하루 전체를 덮는다. 그래서 자리를 실제로 좁히는 쪽은 뒤타임이고,
# 제약이 걸리는지 보는 시험은 뒤타임으로 쓴다.
FRONT = band_hours(BAND_FRONT)
BACK = band_hours(BAND_BACK)

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
    assert leader["title"] == "수석"  # 직급 — 동명이인을 가르는 표시


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
    row.availability = {"월": [HOURS[1]]}
    db.commit()

    _upload(client, roster_bytes)
    db.expire_all()
    assert db.get(Interviewer, "IV101").availability == {"월": [HOURS[1]]}


def test_import_accepts_alias_columns(client):
    data = _sheet_bytes([
        ["사원번호", "이름", "직위", "팀", "메일", "하루최대", "우선순위"],
        ["A1", "홍길동", "책임", "AI솔루션팀", "a1@example.com", "5", "1"],
    ])
    result = _upload(client, data).json()["data"]
    assert result["parsed"] == 1
    assert result["interviewers"][0] == {
        "interviewer_id": "A1", "name": "홍길동", "title": "책임",
        "team": "AI솔루션팀",
        "email": "a1@example.com", "max_daily": 5, "priority": 1,
        "time_band": "",   # 가능시간 칸이 없으면 빈칸 — 가용성은 건드리지 않는다
    }


def test_import_uses_defaults_for_blank_numbers(client):
    data = _sheet_bytes([["사번", "성명", "소속팀"], ["A1", "홍길동", "AI솔루션팀"]])
    parsed = _upload(client, data).json()["data"]["interviewers"][0]
    assert parsed["max_daily"] == len(HOURS)
    assert parsed["priority"] == 2
    assert parsed["title"] == ""  # 직급 칸이 없어도 업로드는 통과한다


def test_import_rejects_file_without_headers(client):
    data = _sheet_bytes([["아무말", "대잔치"], ["1", "2"]])
    r = _upload(client, data)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


# ------------------------------------------------------- 가능 시간(앞타임·뒤타임)

def test_front_and_back_cover_every_slot():
    """앞뒤가 겹치므로 아무도 못 맡는 칸이 남지 않는다.

    오전 · 오후로 갈라 받던 시절에는 정오에 걸치는 칸을 어느 쪽도 못 맡아
    점심때가 통째로 비었다. 앞타임을 14시까지, 뒤타임을 12시부터로 두면
    12시 ~ 14시가 겹쳐 그 구멍이 사라진다.
    """
    for timing in ({}, {"start": "10:30", "minutes": 45, "rest": 10},
                   {"start": "09:00", "minutes": 60, "rest": 0}):
        front = set(band_hours(BAND_FRONT, timing))
        back = set(band_hours(BAND_BACK, timing))
        assert front | back == set(HOURS), timing
        assert front & back, timing          # 겹치는 칸이 실제로 있다


def test_import_reads_time_band_column(client, db):
    """명단에 적어 낸 가능시간이 그대로 가용성이 된다.

    현업 엑셀에는 아직 '오전만' 같은 예전 표기가 그대로 온다 — 앞타임 ·
    뒤타임으로 옮겨 받는다.
    """
    data = _sheet_bytes([
        ["사번", "성명", "소속팀", "가능시간"],
        ["A1", "홍길동", "AI솔루션팀", "앞타임"],
        ["A2", "김철수", "AI솔루션팀", "오후"],       # 예전 표기 → 뒤타임
        ["A3", "이영희", "AI솔루션팀", ""],
    ])
    parsed = _upload(client, data).json()["data"]["interviewers"]
    assert [p["time_band"] for p in parsed] == ["앞타임", "뒤타임", ""]

    listed = {i["interviewer_id"]: i for i in client.get("/api/v1/interviewers").json()["data"]}
    assert listed["A1"]["availability"]["월"] == FRONT
    assert listed["A2"]["availability"]["금"] == BACK
    assert listed["A3"]["availability"] == {}   # 안 적었으면 건드리지 않는다
    # 되읽은 표기는 저장된 칸에서 거꾸로 알아낸다. 기본 조건에서는 앞타임이 하루
    # 전체라 '둘 다' 와 구별되지 않으므로 그렇게 나오는 게 맞다.
    assert listed["A1"]["time_band"] == BAND_ALL
    assert listed["A2"]["time_band"] == "뒤타임"


def test_set_bands_updates_availability_and_cap(client, roster_bytes):
    _upload(client, roster_bytes)
    r = client.put("/api/v1/interviewers/bands",
                   json={"bands": {"IV101": "앞타임", "IV102": "뒤타임"}, "actor": "pytest"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["updated"] == 2

    listed = {i["interviewer_id"]: i for i in client.get("/api/v1/interviewers").json()["data"]}
    assert listed["IV101"]["availability"]["월"] == FRONT
    assert listed["IV102"]["time_band"] == "뒤타임"
    # 고른 덩어리가 곧 하루 최대 인원의 천장이다
    assert listed["IV101"]["max_daily"] == len(FRONT)
    assert listed["IV102"]["availability"]["수"] == BACK
    assert listed["IV102"]["max_daily"] == len(BACK)


def test_set_bands_accepts_legacy_names(client, roster_bytes):
    """예전에 쓰던 '오전만 · 오후만' 으로 보내도 지금 이름으로 받아 준다."""
    _upload(client, roster_bytes)
    r = client.put("/api/v1/interviewers/bands",
                   json={"bands": {"IV101": "오전만", "IV102": "오후만"}})
    assert r.status_code == 200, r.text
    listed = {i["interviewer_id"]: i for i in client.get("/api/v1/interviewers").json()["data"]}
    assert listed["IV101"]["availability"]["월"] == FRONT     # 오전만 → 앞타임
    assert listed["IV102"]["time_band"] == "뒤타임"           # 오후만 → 뒤타임


def test_set_bands_rejects_unknown_band(client, roster_bytes):
    _upload(client, roster_bytes)
    r = client.put("/api/v1/interviewers/bands", json={"bands": {"IV101": "새벽만"}})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_FAILED"


def test_ignore_availability_fills_seats_and_names_who_to_call(
    client, db, roster_bytes, sample_round_id
):
    """뒤타임만 되는 담당자뿐일 때 '일정 무시하고 배치하기'가 앞 자리도 쓴다.

    자리는 채워지되 그게 규칙 위반으로 세지면 안 되고, 대신 어긋난 사람이
    누구인지는 명단으로 나와야 한다 — 인사가 개별로 조율할 대상이다.
    """
    _upload(client, roster_bytes)
    everyone = [i["interviewer_id"] for i in client.get("/api/v1/interviewers").json()["data"]]
    client.put("/api/v1/interviewers/bands",
               json={"bands": {iv: "뒤타임" for iv in everyone}, "actor": "pytest"})
    client.put(f"/api/v1/interviewers/rounds/{sample_round_id}",
               json={"interviewer_ids": everyone, "actor": "pytest"})

    def _generate(ignore: bool) -> dict:
        return client.post("/api/v1/schedules/generate", json={
            "round_id": sample_round_id, "plan_id": f"plan-ignore-{ignore}",
            "algorithm": "v5", "constraints": {"ignore_availability": ignore},
        }).json()["data"]

    def _hours(schedule_id: str) -> set[str]:
        rows = client.get(f"/api/v1/schedules/{schedule_id}").json()["data"]["assignments"]
        return {row["hour"] for row in rows}

    kept, ignored = _generate(False), _generate(True)

    # 지키면 뒤타임 칸만 쓰고, 무시하면 그 밖의 칸까지 쓴다
    assert _hours(kept["schedule_id"]) <= set(BACK)
    assert _hours(ignored["schedule_id"]) - set(BACK)
    assert kept["off_band_count"] == 0
    assert ignored["off_band_count"] > 0
    # 일부러 고른 어긋남이므로 규칙 위반으로 세지 않는다
    assert ignored["hard_violations"] == 0
    assert all(row["hour"] in HOURS for row in ignored["off_band"])

    # 다시 검증해도 같은 잣대를 쓴다 (만들 때 켠 설정을 기억한다)
    again = client.post(
        f"/api/v1/schedules/{ignored['schedule_id']}/validate"
    ).json()["data"]
    assert again["hard_violations"] == []
    assert len(again["off_band"]) == ignored["off_band_count"]


def test_band_limits_schedule_hours(client, db, roster_bytes, sample_round_id):
    """부서가 고른 앞타임 · 뒤타임이 실제 배치를 묶는다."""
    _upload(client, roster_bytes)
    interviewer_roster.set_bands(db, {"IV101": "뒤타임", "IV102": "뒤타임"})
    interviewer_roster.select_for_round(db, sample_round_id, ["IV101", "IV102"])

    loaded = {iv.interviewer_id: iv for iv in
              schedule_service.load_interviewers(db, sample_round_id)}
    assert loaded["IV101"].availability["월"] == BACK
    assert not loaded["IV101"].is_available("월", HOURS[0])   # 첫 칸은 뒤타임이 아니다


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


def test_master_hours_survive_when_nobody_answered(client, db, roster_bytes,
                                                   sample_round_id):
    """03 회신이 0건이면 마스터에 적힌 시간이 그대로 살아 있어야 한다.

    03이 죽어 있거나 아직 아무도 회신을 안 하면 목 면접관으로 폴백하게 되어
    있었는데, 여기서는 그 결과를 마스터 가용성과 교집합으로 잡는다. 지어낸
    시간과 실제 시간이 겹치는 칸만 남아, 부서 화면이 보여 준 '수 3~7타임' 이
    스케줄러 안에서는 '수 7타임' 한 칸으로 쪼그라들었다. 그러면 부서가 잡아
    보낸 자리가 절반 넘게 담당자 시간 밖으로 몰려 도로 옮겨진다.
    """
    _upload(client, roster_bytes)
    # 목 IV101 은 7 · 8타임만 되는 사람이다. 마스터가 그 칸에 **한 칸이라도**
    # 걸치면 교집합이 비지 않아 그 한 칸만 남는다 — 아예 안 겹칠 때만 마스터로
    # 되돌리는 지금 규칙에 가려져 있던 자리다.
    wanted = {"수": [HOURS[2], HOURS[3], HOURS[4], HOURS[5], HOURS[6]]}
    db.get(Interviewer, "IV101").availability = dict(wanted)
    db.commit()
    interviewer_roster.select_for_round(db, sample_round_id, ["IV101", "IV102"])

    loaded = {iv.interviewer_id: iv for iv in
              schedule_service.load_interviewers(db, sample_round_id)}

    assert loaded["IV101"].availability == wanted


# ------------------------------------- 옛 시각 이름으로 저장된 자료 (칸 이름 개편 이전)

def test_legacy_clock_hours_are_moved_into_current_slots(client, db, roster_bytes,
                                                         sample_round_id):
    """'09시' 같은 옛 이름이 남아 있어도 그 사람이 통째로 빠지면 안 된다.

    칸 이름을 자리 번호로 바꾸기 전에 저장된 DB 를 그대로 열면, 저장된 시간이
    지금 쓰는 어느 칸에도 안 걸려 배정 인원이 0명이 된다. 그래서 읽을 때
    그 이름이 뜻하던 앞/뒤만 살려 지금 칸으로 옮긴다.
    """
    _upload(client, roster_bytes)
    db.get(Interviewer, "IV101").availability = {"월": ["09시", "10시", "11시"]}
    db.get(Interviewer, "IV102").availability = {"화": ["14시", "15시"]}
    db.get(Interviewer, "IV103").availability = {"수": ["09시", "15시"]}
    db.commit()
    interviewer_roster.select_for_round(db, sample_round_id, ["IV101", "IV102", "IV103"])

    # 옮기는 규칙 자체
    assert normalize_availability({"월": ["09시", "10시", "11시"]}) == {"월": FRONT}
    assert normalize_availability({"화": ["14시", "15시"]}) == {"화": BACK}
    assert normalize_availability({"수": ["09시", "15시"]}) == {"수": list(HOURS)}

    # 읽어 들인 뒤에도 옛 이름이 남지 않고, 그 사람이 통째로 빠지지도 않는다.
    # (회신이 함께 오면 교집합을 잡으므로 칸이 더 줄어들 수는 있다.)
    loaded = {iv.interviewer_id: iv for iv in
              schedule_service.load_interviewers(db, sample_round_id)}
    for interviewer_id, day in (("IV101", "월"), ("IV102", "화"), ("IV103", "수")):
        hours = loaded[interviewer_id].availability.get(day, [])
        assert hours, interviewer_id
        assert set(hours) <= set(HOURS), interviewer_id
    assert set(loaded["IV102"].availability["화"]) <= set(BACK)
