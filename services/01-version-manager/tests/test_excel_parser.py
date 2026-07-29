"""엑셀 파서 테스트 — 실제 파일 기준."""
from app.services.excel_parser import extract_applicant_ids


def test_master_has_467_applicants(master_bytes):
    ids = extract_applicant_ids(master_bytes)
    assert len(ids) == 467
    assert len(set(ids)) == 467  # 중복 없음
    assert all(i.isdigit() for i in ids)


def test_team_files_parse(team_files):
    counts = {t: len(extract_applicant_ids(b)) for t, b in team_files.items()}
    # 팀별 지원자 수 (실데이터)
    assert counts["AI솔루션팀"] == 16
    assert counts["로봇응용기술팀"] == 19
    assert counts["미래혁신팀"] == 17
    assert counts["배터리기술팀"] == 16
    assert counts["전극기술팀"] == 20


def test_empty_or_garbage_returns_empty():
    assert extract_applicant_ids(b"not an excel file") == []
