"""엑셀에서 지원자 ID 추출.

실제 취합/배포 엑셀 구조:
  - 상단에 병합 카테고리 행이 있고, "지원자 번호" 헤더가 특정 행에 존재
  - 헤더 행/정크 행이 중간에 반복될 수 있어 방어적으로 파싱
전략:
  1) "지원자 번호" 라벨이 있는 첫 셀을 찾아 (헤더행, ID컬럼) 결정
  2) 그 아래 행들에서 ID컬럼 값이 숫자면 지원자 ID로 채택 (헤더 반복행/빈행 스킵)
  3) 등장 순서를 유지하되 중복 제거
"""
from io import BytesIO

import pandas as pd

ID_HEADER = "지원자 번호"


def _norm(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none"):
        return ""
    return s


def _to_id(v) -> str | None:
    s = _norm(v)
    if not s:
        return None
    if s.endswith(".0"):
        s = s[:-2]
    if s.isdigit():
        return s
    return None


def _locate_header(raw: pd.DataFrame) -> tuple[int, int] | None:
    """(header_row, id_col) 위치 탐색. 못 찾으면 None."""
    max_scan = min(15, len(raw))
    for r in range(max_scan):
        for c in range(raw.shape[1]):
            if _norm(raw.iat[r, c]) == ID_HEADER:
                return r, c
    return None


def extract_applicant_ids(data: bytes) -> list[str]:
    """엑셀 바이트에서 지원자 ID 목록(순서 유지, 중복 제거)을 추출."""
    try:
        raw = pd.read_excel(BytesIO(data), header=None, dtype=str)
    except Exception:
        # 엑셀이 아니거나 손상된 파일 → 지원자 ID 없음
        return []
    loc = _locate_header(raw)
    if loc is None:
        return []
    header_row, id_col = loc

    ids: list[str] = []
    seen: set[str] = set()
    for r in range(header_row + 1, len(raw)):
        cell = raw.iat[r, id_col]
        # 헤더 반복행 스킵
        if _norm(cell) == ID_HEADER:
            continue
        aid = _to_id(cell)
        if aid and aid not in seen:
            seen.add(aid)
            ids.append(aid)
    return ids
