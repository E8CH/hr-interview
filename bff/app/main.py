"""
HR Interview System - BFF (Backend For Frontend)
- UI가 이 계층 하나만 호출하도록 설계
- 내부적으로 7개 서비스를 조합
Usage: uvicorn app.main:app --port 8000 --reload
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional
from .workflows import run_full_round, get_round_timeline, get_round_dashboard
from . import clients

app = FastAPI(title="HR Interview BFF", version="1.0.0")

class FullRoundRequest(BaseModel):
    round_id: str
    source: str = "bff"
    notes: Optional[str] = None
    # 서버에서 읽을 마스터 엑셀 경로 (미지정 시 docs/취합파일.xlsx)
    master_path: Optional[str] = None

@app.get("/")
async def root():
    return {"service": "bff", "version": "1.0.0"}

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

@app.get("/api/v1/services/health")
async def services_health():
    """7개 다운스트림 서비스 상태 집계"""
    return await clients.all_health()

@app.post("/api/v1/workflows/full-round")
async def workflow_full_round(req: FullRoundRequest):
    """Version → Distribute → Schedule → Audit 전체 시나리오"""
    result = await run_full_round(req.round_id, req.source, req.notes, req.master_path)
    if result["status"] == "fail":
        raise HTTPException(status_code=502, detail=result)
    return result

@app.get("/api/v1/workflows/{round_id}/timeline")
async def workflow_timeline(round_id: str):
    return await get_round_timeline(round_id)

@app.get("/api/v1/workflows/{round_id}/dashboard")
async def workflow_dashboard(round_id: str):
    return await get_round_dashboard(round_id)
