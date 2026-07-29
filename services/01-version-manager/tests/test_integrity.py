"""무결성 검증 로직 테스트 (순수 함수 + 실데이터 시나리오)."""
from app.services.integrity_checker import (
    STATUS_ISSUES,
    STATUS_NO_MASTER,
    STATUS_OK,
    check_integrity,
)


def test_no_master():
    result = check_integrity(None, {"AI솔루션팀": ["1", "2"]})
    assert result["status"] == STATUS_NO_MASTER


def test_no_master_empty_list():
    assert check_integrity([], {})["status"] == STATUS_NO_MASTER


def test_all_ok():
    master = ["1", "2", "3", "4"]
    teams = {"A": ["1", "2"], "B": ["3", "4"]}
    result = check_integrity(master, teams)
    assert result["status"] == STATUS_OK
    assert result["undistributed_count"] == 0
    assert result["duplicate_count"] == 0
    assert result["distributed_count"] == 4


def test_duplicate_distribution():
    master = ["1", "2", "3"]
    teams = {"A": ["1", "2"], "B": ["2", "3"]}  # 2가 두 팀에
    result = check_integrity(master, teams)
    assert result["status"] == STATUS_ISSUES
    assert result["duplicate_count"] == 1
    dup = [i for i in result["issues"] if i["type"] == "DUPLICATE_DISTRIBUTION"]
    assert dup[0]["applicant_id"] == "2"
    assert set(dup[0]["teams"]) == {"A", "B"}


def test_undistributed():
    master = ["1", "2", "3", "4", "5"]
    teams = {"A": ["1"]}
    result = check_integrity(master, teams)
    assert result["status"] == STATUS_ISSUES
    assert result["undistributed_count"] == 4


def test_real_data_scenario(master_bytes, team_files):
    """실제 6개 파일: 중복 5건, 미배포 384명 자동 감지."""
    from app.services.excel_parser import extract_applicant_ids

    master = extract_applicant_ids(master_bytes)
    teams = {t: extract_applicant_ids(b) for t, b in team_files.items()}
    result = check_integrity(master, teams)

    assert result["status"] == STATUS_ISSUES
    assert result["master_count"] == 467
    assert result["distributed_count"] == 83
    assert result["undistributed_count"] == 384
    assert result["duplicate_count"] == 5
    # 명세 API 예시의 중복자 확인
    dup_ids = {i["applicant_id"] for i in result["issues"]
               if i["type"] == "DUPLICATE_DISTRIBUTION"}
    assert "3672536" in dup_ids
