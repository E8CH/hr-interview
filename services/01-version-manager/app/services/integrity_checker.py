"""회차 무결성 검증 로직 (순수 함수).

입력:
  master_ids: 마스터(취합파일)의 지원자 ID 목록
  team_map:   {team_name: [applicant_id, ...]} 활성 배포본
출력: 명세 verify 응답의 data 필드 형태 dict

판정:
  - master 없음 → status=NO_MASTER
  - 중복(두 팀 이상 배포) 또는 미배포 존재 → ISSUES_FOUND
  - 그 외 → OK
"""
from collections import OrderedDict

STATUS_OK = "OK"
STATUS_ISSUES = "ISSUES_FOUND"
STATUS_NO_MASTER = "NO_MASTER"


def check_integrity(master_ids: list[str] | None,
                    team_map: dict[str, list[str]]) -> dict:
    if not master_ids:
        return {
            "status": STATUS_NO_MASTER,
            "master_count": 0,
            "distributed_count": 0,
            "undistributed_count": 0,
            "duplicate_count": 0,
            "issues": [],
        }

    master_set = set(master_ids)

    # 지원자별 배포된 팀 목록 (등장 순서 유지)
    id_to_teams: "OrderedDict[str, list[str]]" = OrderedDict()
    for team, ids in team_map.items():
        for aid in dict.fromkeys(ids):  # 팀 내 중복 제거
            id_to_teams.setdefault(aid, [])
            if team not in id_to_teams[aid]:
                id_to_teams[aid].append(team)

    distributed_set = set(id_to_teams.keys())
    distributed_count = len(distributed_set)

    undistributed = [aid for aid in master_ids if aid not in distributed_set]
    duplicates = {aid: teams for aid, teams in id_to_teams.items() if len(teams) >= 2}
    unknown = [aid for aid in id_to_teams if aid not in master_set]

    issues: list[dict] = []
    for aid, teams in duplicates.items():
        issues.append({
            "type": "DUPLICATE_DISTRIBUTION",
            "applicant_id": aid,
            "teams": teams,
        })
    for aid in unknown:
        issues.append({
            "type": "UNKNOWN_APPLICANT",
            "applicant_id": aid,
            "teams": id_to_teams[aid],
        })
    if undistributed:
        issues.append({
            "type": "UNDISTRIBUTED",
            "count": len(undistributed),
            "applicant_ids": undistributed,
        })

    duplicate_count = len(duplicates)
    undistributed_count = len(undistributed)
    status = STATUS_ISSUES if (duplicate_count or undistributed_count or unknown) else STATUS_OK

    return {
        "status": status,
        "master_count": len(master_set),
        "distributed_count": distributed_count,
        "undistributed_count": undistributed_count,
        "duplicate_count": duplicate_count,
        "issues": issues,
    }
