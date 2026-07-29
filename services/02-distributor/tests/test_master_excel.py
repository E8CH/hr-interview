"""취합파일 파서 테스트 (실파일 + 이상 케이스)."""
import pytest
from openpyxl import Workbook

from app.infrastructure.master_excel import (
    COLUMN_MAP,
    MasterFileError,
    load_master_excel,
    load_team_roster,
)

HEADERS = list(COLUMN_MAP.values())


def _write(path, header, rows, group_row=True):
    workbook = Workbook()
    sheet = workbook.active
    if group_row:
        sheet.append(["그룹헤더"] * len(header))  # 1행: 병합 그룹 헤더
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    return path


def _row(applicant_id, name="김민준"):
    values = {
        "지원자 번호": applicant_id,
        "한글성명": name,
        "1지망_조직": "제1기술원",
        "1지망_직무": "직무다",
        "R&D/N-R&D": "구분R",
        "최종학력_학교유형": "과정2",
        "최종학력_주전공": "푸른화학과",
        "학사1_주전공": "푸른화학과",
        "최종학력_환산학점": 4.1,
        "타겟랩여부": "Y",
        "지도교수": "이교수",
        "이전 지원 전체 횟수": 1,
        "1차서류 결과": "결과P",
    }
    return [values[h] for h in HEADERS]


def test_real_master_columns_are_populated(master_applicants):
    first = master_applicants[0]
    assert first.applicant_id and first.team_1st and first.job_1st
    assert all(a.doc_result == "결과P" for a in master_applicants)
    assert all(a.rnd_type == "구분R" for a in master_applicants)
    # 선택 컬럼도 최소한 일부는 채워져 있어야 파싱이 제대로 된 것
    assert any(a.major_final for a in master_applicants)
    assert any(a.gpa_final for a in master_applicants)


def test_real_team_rosters_are_nonempty(team_truth):
    for team, ids in team_truth.items():
        assert ids, team
        assert all(i.isdigit() for i in ids)


def test_skips_repeated_header_and_duplicate_ids(tmp_path):
    path = _write(
        tmp_path / "m.xlsx",
        HEADERS,
        [_row("100"), HEADERS, _row("100", "동일인"), _row("101"), [None] * len(HEADERS)],
    )
    applicants = load_master_excel(path)
    assert [a.applicant_id for a in applicants] == ["100", "101"]
    assert applicants[0].name == "김민준"  # 첫 등장 우선


def test_duplicate_column_names_use_first_occurrence(tmp_path):
    header = HEADERS + ["지원자 번호"]
    row = _row("200") + ["999"]
    path = _write(tmp_path / "dup.xlsx", header, [row])
    assert load_master_excel(path)[0].applicant_id == "200"


def test_malformed_numeric_cells_degrade_gracefully(tmp_path):
    row = _row("300")
    row[HEADERS.index("최종학력_환산학점")] = "N/A"
    row[HEADERS.index("이전 지원 전체 횟수")] = ""
    path = _write(tmp_path / "bad.xlsx", HEADERS, [row])
    applicant = load_master_excel(path)[0]
    assert applicant.gpa_final is None
    assert applicant.prev_applications == 0


def test_missing_required_column_raises(tmp_path):
    header = [h for h in HEADERS if h != "1차서류 결과"]
    path = _write(tmp_path / "missing.xlsx", header, [_row("400")[: len(header)]])
    with pytest.raises(MasterFileError, match="필수 컬럼"):
        load_master_excel(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(MasterFileError, match="없습니다"):
        load_master_excel(tmp_path / "nope.xlsx")


def test_header_only_file_raises(tmp_path):
    path = _write(tmp_path / "empty.xlsx", HEADERS, [])
    with pytest.raises(MasterFileError, match="0명"):
        load_master_excel(path)


def test_no_header_row_raises(tmp_path):
    workbook = Workbook()
    workbook.active.append(["그룹헤더"])
    workbook.save(tmp_path / "onerow.xlsx")
    with pytest.raises(MasterFileError, match="데이터가 없습니다"):
        load_master_excel(tmp_path / "onerow.xlsx")


def test_team_roster_skips_header_and_blanks(tmp_path):
    path = _write(
        tmp_path / "roster.xlsx",
        ["지원자 번호", "한글성명"],
        [["111", "김민준"], ["지원자 번호", "한글성명"], [None, None], ["222", "이서연"]],
    )
    assert load_team_roster(path) == ["111", "222"]
