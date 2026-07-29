"""Service 02 — Distributor.

`app` 패키지를 import하는 즉시 shared/contracts 경로를 sys.path에 등록한다.
(공통 계약은 읽기 전용이므로 vendoring 대신 경로 참조 방식을 쓴다.)
"""
from app.config import settings  # noqa: F401  (import 부수효과: sys.path 부트스트랩)
