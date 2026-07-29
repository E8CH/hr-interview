"""Before/After 시뮬레이션 실행 스크립트

    python scripts/simulate.py [--n 200] [--seed 20260729] [--json]

명세 완료 판정: "Before/After 시뮬레이션: 회신 소요 30h → 12h 재현"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.simulation import (  # noqa: E402
    SimulationParams,
    format_report,
    run_simulation,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="회신 소요시간 Before/After 시뮬레이션")
    parser.add_argument("--n", type=int, default=200, help="면접위원 수 (기본 200)")
    parser.add_argument("--seed", type=int, default=20260729, help="난수 시드")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    result = run_simulation(SimulationParams(n_invitees=args.n, seed=args.seed))

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_report(result))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
