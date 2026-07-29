"""Service 03 — Response Collector

`shared/contracts`(읽기 전용 공통 계약)를 import 할 수 있도록 리포지토리 루트를
sys.path 에 추가한다.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

__all__ = ["_REPO_ROOT"]
