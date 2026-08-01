"""업무 콘솔(tools/test_console.py)의 순수 함수만 꺼내 쓰는 시험 준비

콘솔은 streamlit 앱이라 import 하는 순간 화면을 그리기 시작한다. 그래서
streamlit 은 흉내만 내고, 소스에서 **화면을 그리기 시작하는 줄 앞까지** 만
실행해 계산 함수들을 얻는다. 콘솔 전용 venv(.venv-ui)에는 pytest 가 없고,
서비스 venv(.venv)에는 streamlit 이 없다 — 흉내가 둘을 잇는다.

실행:
    .venv/Scripts/python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "tools" / "test_console.py"

#: 이 줄부터는 화면을 그린다 — 여기서 자른다
DRAW_STARTS_WITH = "brand_bar("


def _fake_streamlit() -> MagicMock:
    """계산 함수가 부르는 만큼만 흉내 낸다."""
    fake = MagicMock(name="streamlit")

    def cache(*args, **kwargs):
        # 데코레이터는 원본 함수를 그대로 돌려줘야 한다. 콘솔이 `fn.clear()` 로
        # 캐시를 비우는 곳이 있어 그 자리도 채워 둔다.
        def wrap(fn):
            fn.clear = lambda *_a, **_k: None
            return fn
        return wrap(args[0]) if args and callable(args[0]) else wrap

    fake.cache_data = cache
    fake.cache_resource = cache
    fake.session_state = {}
    fake.columns = lambda spec, **kwargs: [
        MagicMock() for _ in (range(spec) if isinstance(spec, int) else spec)]
    return fake


@pytest.fixture(scope="session")
def console():
    """콘솔의 계산 함수들이 담긴 모듈."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.modules["streamlit"] = _fake_streamlit()
    sys.modules["streamlit.components"] = MagicMock()
    sys.modules["streamlit.components.v1"] = MagicMock()

    lines = CONSOLE.read_text(encoding="utf-8").splitlines()
    cut = next(i for i, line in enumerate(lines)
               if line.startswith(DRAW_STARTS_WITH))
    module = types.ModuleType("console_pure")
    module.__file__ = str(CONSOLE)
    exec(compile("\n".join(lines[:cut]), module.__file__, "exec"),
         module.__dict__)
    return module
