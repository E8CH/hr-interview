"""
서비스별 HTTP 클라이언트 — 실제 경로 흡수 지점
계약 바뀌면 여기만 수정하면 UI/워크플로우는 그대로.

응답은 모두 공통 봉투 {"data": ..., "error": ...} 라서 `unwrap()` 으로 data 만 돌려준다.
"""
from typing import Any, Optional

import httpx

VERSION_MANAGER    = "http://localhost:8001"
DISTRIBUTOR        = "http://localhost:8002"
RESPONSE_COLLECTOR = "http://localhost:8003"
SCHEDULER          = "http://localhost:8004"
REPAIR_ENGINE      = "http://localhost:8005"
NOTIFICATION_HUB   = "http://localhost:8006"
AUDIT              = "http://localhost:8007"

TIMEOUT = 60.0
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def unwrap(response: httpx.Response) -> Any:
    """공통 응답 봉투에서 data 를 꺼낸다 (봉투가 아니면 원본 그대로)."""
    body = response.json()
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


async def all_health():
    services = [
        ("version-manager", VERSION_MANAGER),
        ("distributor", DISTRIBUTOR),
        ("response-collector", RESPONSE_COLLECTOR),
        ("scheduler", SCHEDULER),
        ("repair-engine", REPAIR_ENGINE),
        ("notification-hub", NOTIFICATION_HUB),
        ("audit-analytics", AUDIT),
    ]
    results = {}
    async with httpx.AsyncClient(timeout=1.5) as c:
        for name, base in services:
            try:
                r = await c.get(f"{base}/healthz")
                results[name] = {"status": "ok" if r.status_code == 200 else "warn", "code": r.status_code}
            except Exception as e:
                results[name] = {"status": "down", "error": type(e).__name__}
    return results


async def register_version(
    round_id: str,
    file_name: str,
    file_bytes: bytes,
    actor: str = "bff",
    kind: str = "master",
    team_name: Optional[str] = None,
):
    """Service 01 — 마스터/팀 엑셀 등록 (multipart/form-data)."""
    data = {"round_id": round_id, "kind": kind, "actor": actor}
    if team_name:
        data["team_name"] = team_name
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            f"{VERSION_MANAGER}/api/v1/versions/register",
            files={"file": (file_name, file_bytes, XLSX_MIME)},
            data=data,
        )
        r.raise_for_status()
        return unwrap(r)


async def create_distribution(round_id: str, master_version_id: str, created_by: str = "bff"):
    """Service 02 — 배포안 생성 (master_version_id 필수)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            f"{DISTRIBUTOR}/api/v1/distribute/plan",
            json={
                "round_id": round_id,
                "master_version_id": master_version_id,
                "created_by": created_by,
            },
        )
        r.raise_for_status()
        return unwrap(r)


async def generate_schedule(
    round_id: str,
    plan_id: str,
    algorithm: str = "v5",
    generated_by: str = "bff",
):
    """Service 04 — 시간표 생성 (plan_id 필수, null 불가)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(
            f"{SCHEDULER}/api/v1/schedules/generate",
            json={
                "round_id": round_id,
                "plan_id": plan_id,
                "algorithm": algorithm,
                "generated_by": generated_by,
            },
        )
        r.raise_for_status()
        return unwrap(r)


async def get_timeline(round_id: str):
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(
            f"{AUDIT}/api/v1/audit/timeline",
            params={"round_id": round_id},
        )
        r.raise_for_status()
        return unwrap(r)


async def get_kpi():
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{AUDIT}/api/v1/dashboard/kpi")
        r.raise_for_status()
        return unwrap(r)
