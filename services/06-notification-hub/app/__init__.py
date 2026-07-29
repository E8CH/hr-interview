"""Service 06 — Notification Hub

`shared/` (읽기 전용 공통 계약 · 감사 싱크)를 import 할 수 있도록 그것을 품은
디렉토리를 sys.path 에 등록한다. (PoC 는 리포지토리 루트, 컨테이너는 /app —
레이아웃 깊이가 다르므로 상위 경로를 탐색해서 찾는다.)
"""
import sys
from pathlib import Path

for _parent in Path(__file__).resolve().parents:
    if (_parent / "shared" / "contracts").is_dir():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break
