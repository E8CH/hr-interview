"""PoC 시드 스크립트 — 데모 회차 이벤트 · baseline KPI 주입

사용:
    python scripts/seed_poc.py

주입 후 확인:
    GET /api/v1/dashboard/kpi?round_id=R2026-Q3-01
    GET /api/v1/reports/before-after?rounds=R2025-Q4-04,R2026-Q3-01
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.infrastructure.db import init_db, new_session  # noqa: E402
from app.services.demo_data import seed_all  # noqa: E402


def main() -> int:
    init_db()
    session = new_session()
    try:
        summary = seed_all(session)
    finally:
        session.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
