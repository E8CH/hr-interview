"""Service 01 — Version Manager.

repo 루트를 sys.path에 추가해 공통 계약(`shared.contracts.*`)을 import 가능하게 한다.
services/01-version-manager/app/__init__.py 기준 parents[3] == repo 루트.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
