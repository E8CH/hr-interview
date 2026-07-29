"""완료 판정 데모 — 실행 중인 서버에 6개 엑셀을 등록하고 무결성 검증.

사용:
  uvicorn app.main:app --port 8001   # 다른 터미널
  python scripts/demo_verify.py [BASE_URL]

기대 결과: master_count=467, undistributed=384, duplicate=5, status=ISSUES_FOUND
"""
import sys
from pathlib import Path

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
ROUND = "R2026-Q3-01"
DOCS = Path(__file__).resolve().parents[3] / "docs"

TEAMS = {
    "AI솔루션팀": "희망지원자_AI솔루션팀.xlsx",
    "로봇응용기술팀": "희망지원자_로봇응용기술팀.xlsx",
    "미래혁신팀": "희망지원자_미래혁신팀.xlsx",
    "배터리기술팀": "희망지원자_배터리기술팀.xlsx",
    "전극기술팀": "희망지원자_전극기술팀.xlsx",
}


def register(client, path, kind, actor, team_name=None):
    files = {"file": (path.name, path.read_bytes())}
    data = {"round_id": ROUND, "kind": kind, "actor": actor}
    if team_name:
        data["team_name"] = team_name
    r = client.post(f"{BASE}/api/v1/versions/register", files=files, data=data)
    r.raise_for_status()
    d = r.json()["data"]
    print(f"  등록 {kind:18} {team_name or '-':10} → {d['version_id']} "
          f"(지문 {d['fingerprint']}, {d['applicant_count']}명)")
    return d


def main():
    with httpx.Client(timeout=30) as client:
        print("== 마스터 + 5개 팀 배포본 등록 ==")
        register(client, DOCS / "취합파일.xlsx", "master", "HR김민지")
        for team, fname in TEAMS.items():
            register(client, DOCS / fname, "team_distribution", "HR김민지", team_name=team)

        print("\n== 무결성 검증 ==")
        r = client.post(f"{BASE}/api/v1/versions/verify/{ROUND}")
        r.raise_for_status()
        d = r.json()["data"]
        print(f"  status={d['status']}  master={d['master_count']}  "
              f"distributed={d['distributed_count']}  "
              f"undistributed={d['undistributed_count']}  duplicate={d['duplicate_count']}")
        dups = [i for i in d["issues"] if i["type"] == "DUPLICATE_DISTRIBUTION"]
        print(f"  중복 배포 {len(dups)}건:")
        for i in dups:
            print(f"    - {i['applicant_id']}: {i['teams']}")

        ok = (d["master_count"] == 467 and d["undistributed_count"] == 384
              and d["duplicate_count"] == 5 and d["status"] == "ISSUES_FOUND")
        print("\n판정:", "[PASS]" if ok else "[FAIL]")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
