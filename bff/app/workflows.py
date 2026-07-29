"""
시나리오 워크플로우 조립

Version(01) → Distribute(02) → Schedule(04) → Audit(07)
각 단계의 출력(version_id → plan_id)이 다음 단계의 필수 입력이라 그대로 이어붙인다.
"""
import asyncio
from pathlib import Path
from typing import Optional

from . import clients

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MASTER = REPO_ROOT / "docs" / "취합파일.xlsx"


def _load_master(master_path: Optional[str]) -> tuple[str, bytes]:
    path = Path(master_path) if master_path else DEFAULT_MASTER
    if not path.is_file():
        raise FileNotFoundError(f"마스터 엑셀을 찾을 수 없습니다: {path}")
    return path.name, path.read_bytes()


async def run_full_round(
    round_id: str,
    source: str,
    notes: Optional[str],
    master_path: Optional[str] = None,
):
    steps = []
    version_id = None
    plan_id = None
    schedule_id = None

    # Step 1: Version register (multipart 업로드)
    try:
        file_name, file_bytes = _load_master(master_path)
        v = await clients.register_version(round_id, file_name, file_bytes, actor=source)
        version_id = v.get("version_id")
        steps.append({"step": "version_register", "status": "ok", "version_id": version_id, "data": v})
    except Exception as e:
        steps.append({"step": "version_register", "status": "fail", "error": str(e)})
        return {"round_id": round_id, "status": "fail", "steps": steps}

    # Step 2: Distribute (master_version_id 필수)
    try:
        d = await clients.create_distribution(round_id, version_id, created_by=source)
        plan_id = d.get("plan_id")
        steps.append({"step": "distribute", "status": "ok", "plan_id": plan_id, "data": d})
    except Exception as e:
        steps.append({"step": "distribute", "status": "fail", "error": str(e)})
        return {"round_id": round_id, "status": "partial", "steps": steps}

    # Step 3: Schedule (plan_id 필수)
    try:
        s = await clients.generate_schedule(round_id, plan_id, generated_by=source)
        schedule_id = s.get("schedule_id")
        steps.append({"step": "schedule", "status": "ok", "schedule_id": schedule_id, "data": s})
    except Exception as e:
        steps.append({"step": "schedule", "status": "fail", "error": str(e)})
        return {"round_id": round_id, "status": "partial", "steps": steps}

    # Step 4: Audit (이벤트 포워딩은 비동기라 잠깐 재시도)
    try:
        events = []
        for _ in range(10):
            events = await clients.get_timeline(round_id) or []
            if events:
                break
            await asyncio.sleep(0.3)
        steps.append({"step": "audit", "status": "ok", "event_count": len(events)})
    except Exception as e:
        steps.append({"step": "audit", "status": "fail", "error": str(e)})

    return {
        "round_id": round_id,
        "status": "success",
        "version_id": version_id,
        "plan_id": plan_id,
        "schedule_id": schedule_id,
        "steps": steps,
    }


async def get_round_timeline(round_id: str):
    return await clients.get_timeline(round_id)


async def get_round_dashboard(round_id: str):
    kpi = await clients.get_kpi()
    timeline = await clients.get_timeline(round_id)
    return {"round_id": round_id, "kpi": kpi, "timeline": timeline}
