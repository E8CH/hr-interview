"""
HR Interview System — 업무 콘솔 (v4)

업무 순서 그대로 5개 메뉴로 구성한다.
    1. 자료취합 버전관리   — 취합본 여러 개 대조 → 행 채택 → 최종 파일 생성 (01)
    2. 지원자 명단 정리     — 팀 배포안 생성 · 검수 · 확정 (02)
    3. 면접 담당자 선별     — 면접관명단 업로드 → 회차 투입 인원 선별 (04)
    4. 희망자 취합         — 가능 일정 요청 발송 → 회신·가용성 집계 (03)
    5. 면접 일정 분배       — 시간표 생성 → 배정 결과·규칙 준수 확인 (04)
운영 점검용(헬스체크 · 이벤트 타임라인 · DB 조회 · KPI · 시나리오 일괄 실행)은
좌측 "관리자" 메뉴로 뺀다.

Usage: streamlit run tools/test_console.py
"""
from __future__ import annotations

import io
import json
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

st.set_page_config(page_title="LG 면접 진행 도우미", page_icon="🔴", layout="wide")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 면접일 · 칸 · 앞타임/뒤타임은 서비스와 같은 정의를 써야 한다. 여기서 따로 적어 두면
# 스케줄러가 여덟 칸을 쓰는데 화면만 여섯 칸으로 그리는 어긋남이 다시 생긴다.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from shared.contracts.constants import (  # noqa: E402
    BAND_ALL,
    BAND_BACK,
    BAND_FRONT,
    BAND_NONE,
    DAYS as SCHED_DAYS,
    DAYS_PER_TEAM as SCHED_DAYS_PER_TEAM,
    DEFAULT_TIMING,
    HOURS as SCHED_HOURS,
    band_hours,
    band_name,
    band_of as contract_band_of,
    hour_bands,
    normalize_availability,
    plan_team_days,
)
DEFAULT_MASTER = PROJECT_ROOT / "docs" / "취합파일.xlsx"
INTERVIEWER_SAMPLE = PROJECT_ROOT / "tools" / "fixtures" / "면접관명단_sample.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 화면에 날을 늘어놓는 차례 — 달력에 있는 날이 전부다. 그 밖의 이름이 자료에
# 섞여 있으면 화면 뒤쪽으로 밀리거나 필터에서 빠진다.
DAY_ORDER = list(SCHED_DAYS)

SERVICES = [
    ("version-manager", 8001),
    ("distributor", 8002),
    ("response-collector", 8003),
    ("scheduler", 8004),
    ("repair-engine", 8005),
    ("notification-hub", 8006),
    ("audit-analytics", 8007),
]

# 주소는 반드시 127.0.0.1 로 쓴다. Windows 에서 "localhost" 는 ::1(IPv6) 로 먼저
# 해석되는데 uvicorn 은 IPv4 로만 바인딩돼 있어서, 모든 요청이 IPv6 연결 실패를
# 기다렸다가 IPv4 로 재시도한다 — 호출 하나당 약 2.2초가 그냥 날아간다.
VERSION_MANAGER = "http://127.0.0.1:8001"
DISTRIBUTOR = "http://127.0.0.1:8002"
COLLECTOR = "http://127.0.0.1:8003"
SCHEDULER = "http://127.0.0.1:8004"
REPAIR_ENGINE = "http://127.0.0.1:8005"
NOTIFIER = "http://127.0.0.1:8006"
AUDIT = "http://127.0.0.1:8007"


# ============================================================
# 화면 톤앤매너 — LG전자 스타일 (흰 바탕 · 넉넉한 여백 · 알약 버튼 · LG Active Red)
# ============================================================
LG_RED = "#A50034"          # LG Active Red — 브랜드 강조색
LG_RED_DARK = "#7A0026"
LG_INK = "#111111"          # 본문 글자
LG_SUB = "#6B6B6B"          # 보조 글자
LG_LINE = "#E3E3E3"         # 실선
LG_SURFACE = "#F5F5F5"      # 카드 바탕
LG_FONT = ("'LG Smart UI','Pretendard','Noto Sans KR','Malgun Gothic',"
           "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif")

# 부서(팀)마다 카드 색을 달리한다. 팀 이름을 가나다순으로 줄 세워 번호를 매기므로
# 같은 회차 안에서는 어느 화면에서 보든 같은 팀이 같은 색으로 나온다.
# 색은 LG 톤에 맞춰 채도를 낮춘 계열로 고른다 (첫 자리는 LG Active Red).
TEAM_PALETTE = [
    "#A50034", "#1B6CA8", "#2E7D6B", "#8A5A2B",
    "#5B4B8A", "#0F7A8C", "#6E7B22", "#B4477E",
]

# 학력은 카드 위쪽 띠줄 + 작은 뱃지로 알아본다 (박사 · 석사 · 학사).
# 색은 LG 화면의 분홍 · 민트 · 회색 카드 톤을 그대로 가져온다.
DEGREE_STRIPE = {
    "박사": ("dgd", "#A50034", "#FBEAF0"),      # LG Active Red · 분홍 바탕
    "석사": ("dgm", "#17594E", "#E7F1EE"),      # 딥 민트 · 민트 바탕
    "대학원": ("dgm", "#17594E", "#E7F1EE"),
    "학사": ("dgb", "#4B4B4B", "#F0F0F0"),      # 잉크 그레이 · 회색 바탕
    "미상": ("dgx", "#9E9E9E", "#F7F7F7"),
}

_TEAM_TINT = "\n".join(
    f".hrcard.tm{i}{{border-color:{c}33;background:{c}0f;}}"
    f".hrhead.tm{i}{{border-color:{c}33;background:{c}14;color:{c};}}"
    for i, c in enumerate(TEAM_PALETTE)
)
_DEGREE_TINT = "\n".join(
    f".hrcard.{cls}{{border-top-color:{line};}}"
    f".hrdeg.{cls}{{color:{line};background:{fill};}}"
    for cls, line, fill in {v[0]: v for v in DEGREE_STRIPE.values()}.values()
)

st.markdown(f"""
<style>
:root {{--lg-red:{LG_RED}; --lg-ink:{LG_INK}; --lg-sub:{LG_SUB};
        --lg-line:{LG_LINE}; --lg-surface:{LG_SURFACE};}}

/* ---------- 바탕과 글꼴 ---------- */
html, body, [class*="css"], .stApp {{font-family:{LG_FONT};}}
.stApp {{background:#FFFFFF; color:var(--lg-ink);}}
.block-container {{padding-top:1.6rem; padding-bottom:3rem; max-width:1500px;}}
h1,h2,h3,h4 {{letter-spacing:-.02em; color:var(--lg-ink); font-weight:700;}}
h1 {{font-size:1.72rem;}} h2 {{font-size:1.34rem;}} h3 {{font-size:1.08rem;}}
[data-testid="stCaptionContainer"] {{color:var(--lg-sub);}}
hr {{border-color:var(--lg-line);}}

/* ---------- 브랜드 머리띠 ---------- */
.lgbar {{display:flex; align-items:center; gap:12px; padding:2px 0 14px;
         border-bottom:1px solid var(--lg-line); margin-bottom:20px;}}
.lgmark {{font-weight:800; font-size:1.02rem; letter-spacing:-.04em;
          color:#fff; background:var(--lg-red); border-radius:999px;
          padding:6px 13px; line-height:1;}}
.lgbar .ttl {{font-weight:700; font-size:1.02rem; letter-spacing:-.02em;}}
.lgbar .sub {{color:var(--lg-sub); font-size:.82rem; margin-left:auto;}}

/* ---------- 알약 버튼 ---------- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    border-radius:999px; border:1px solid var(--lg-line); background:#fff;
    color:var(--lg-ink); font-weight:600; letter-spacing:-.01em;
    padding:.46rem 1.05rem; transition:.15s;}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    border-color:var(--lg-ink); color:var(--lg-ink); background:#FAFAFA;}}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
.stDownloadButton > button[kind="primary"] {{
    background:var(--lg-red); border-color:var(--lg-red); color:#fff;}}
.stButton > button[kind="primary"]:hover {{
    background:{LG_RED_DARK}; border-color:{LG_RED_DARK}; color:#fff;}}

/* ---------- 왼쪽 메뉴 ---------- */
[data-testid="stSidebar"] {{background:#FAFAFA; border-right:1px solid var(--lg-line);}}
[data-testid="stSidebar"] .stButton > button {{text-align:left; justify-content:flex-start;}}

/* '?' 말풍선은 스트림릿이 300px 에서 끊고 나머지를 안쪽으로 밀어 넣는다 —
   진행 순서는 네 단계를 한눈에 봐야 하므로 화면 높이에 맞춰 끝까지 편다. */
[data-testid="stTooltipContent"] {{max-height:80vh;}}

/* ---------- 입력·표 ---------- */
[data-baseweb="input"], [data-baseweb="select"] > div, .stTextArea textarea {{
    border-radius:10px !important;}}
[data-testid="stMetric"] {{background:var(--lg-surface); border-radius:14px;
    padding:14px 16px;}}
[data-testid="stMetricValue"] {{font-weight:700; letter-spacing:-.02em;}}
[data-testid="stDataFrame"] {{border-radius:12px; overflow:hidden;
    border:1px solid var(--lg-line);}}
[data-testid="stExpander"] {{border:1px solid var(--lg-line); border-radius:14px;
    background:#fff;}}
[data-testid="stExpander"] summary {{font-weight:600;}}
.stTabs [data-baseweb="tab-list"] {{gap:6px;}}
.stTabs [data-baseweb="tab"] {{border-radius:999px; padding:6px 16px;
    background:var(--lg-surface);}}
.stTabs [aria-selected="true"] {{background:var(--lg-ink) !important; color:#fff !important;}}

/* ---------- 알림 ---------- */
[data-testid="stAlert"] {{border-radius:14px; border:1px solid var(--lg-line);
    box-shadow:none;}}

/* ---------- 부서 화면 맨 윗줄 알림 ---------- */
.lgnotice {{border:1px solid var(--lg-line); border-radius:16px; padding:14px 16px;
            background:#FFF; margin:0 0 18px;}}
.lgnotice .ttl {{display:flex; align-items:center; gap:8px; font-weight:700;
                 font-size:.95rem; letter-spacing:-.02em; margin-bottom:10px;}}
.lgnotice .dot {{width:8px; height:8px; border-radius:999px; background:var(--lg-red);}}
.lgnotice .cnt {{margin-left:auto; font-size:.72rem; font-weight:700; padding:3px 10px;
                 border-radius:999px; background:#FBEAF0; color:var(--lg-red);}}
.lgnotice .cnt.done {{background:#EFF3F1; color:#17594E;}}
.lgnotice .row {{display:flex; align-items:center; gap:9px; padding:9px 12px;
                 border-radius:12px; font-size:.86rem; margin-top:6px;}}
.lgnotice .row.urgent {{background:#FBEAF0; color:#5E1226;}}
.lgnotice .row.wait {{background:var(--lg-surface); color:#3F3F3F;}}
.lgnotice .row .ic {{font-size:.98rem;}}
.lgnotice .row .go {{margin-left:auto; font-size:.7rem; font-weight:700; color:#fff;
                     background:var(--lg-ink); border-radius:999px; padding:3px 10px;
                     white-space:nowrap;}}

/* ---------- 화면 안에서 '지금 누를 버튼' 안내 ---------- */
.lgstep {{display:flex; align-items:flex-start; gap:10px; margin:0 0 14px;
          padding:11px 14px; border-radius:14px; background:#FBEAF0;
          border:1px solid #F2D3DE; color:#5E1226; font-size:.85rem;
          line-height:1.5;}}
.lgstep.done {{background:#EFF7F4; border-color:#BEDDD3; color:#17594E;}}
.lgstep .n {{flex:0 0 auto; font-weight:700; background:#FFF; border-radius:999px;
             padding:1px 9px; font-size:.75rem;}}
.lgstep b {{font-weight:700;}}

/* ---------- 칸 수를 고정한 격자 (마지막 줄은 빈칸으로 채워 줄을 맞춘다) ---------- */
.hrgrid {{display:grid; gap:10px; margin:8px 0 18px;
          grid-template-columns:repeat(var(--cols,4), minmax(0,1fr));}}
.hrcard {{position:relative; padding:11px 13px 10px; border-radius:14px;
          border:1px solid var(--lg-line); border-top:4px solid #D7D7D7;
          background:var(--lg-surface); min-height:66px; overflow:hidden;}}
.hrcard .t {{font-size:.7rem; color:var(--lg-sub); letter-spacing:0; min-height:15px;}}
.hrdeg {{display:inline-block; font-size:.62rem; font-weight:700; line-height:1.6;
         padding:0 8px; border-radius:999px; margin-right:5px;
         letter-spacing:-.01em; vertical-align:middle;}}
.hrcard .h {{font-weight:700; font-size:.97rem; margin:2px 0 3px;
             letter-spacing:-.02em; color:var(--lg-ink);}}
.hrcard .s {{font-size:.77rem; color:var(--lg-sub); line-height:1.4;}}
.hrcard.empty {{background:#FCFCFC; border-style:dashed; border-top-style:dashed;}}
.hrcard.void {{border:0; background:transparent;}}   /* 줄 맞추는 빈칸 */
.hrcard.fix {{box-shadow:inset 0 0 0 1.5px {LG_RED}44;}}
.hrcard.out {{border-color:#E2C7A8; background:#FBF4EC;}}
.hrcard.done {{border-color:#BEDDD3; background:#EFF7F4;}}
/* 고른 중복면접자 — 여러 팀에 흩어진 같은 사람의 카드를 한눈에 잇는다 */
.hrcard.pick {{box-shadow:0 0 0 2.5px {LG_RED}; background:#FFF5F5;}}
.hrcard.pick .h {{color:{LG_RED};}}
.hrbadge {{position:absolute; top:8px; right:8px; font-size:.62rem; font-weight:700;
           padding:2px 8px; border-radius:999px; background:var(--lg-red);
           color:#fff; letter-spacing:-.01em;}}
.hrday {{font-weight:700; font-size:.94rem; margin:18px 0 4px;
         letter-spacing:-.02em; color:var(--lg-ink);}}

/* ---------- 스케줄러식 시간표 (왼쪽 시간 축 + 팀·일자 세로줄) ---------- */
.hrsched {{overflow-x:auto; margin:8px 0 18px; padding-bottom:6px;}}
.hrsched-in {{display:grid; gap:8px;
              grid-template-columns:104px repeat(var(--cols,1), minmax(158px,1fr));}}
.hrhead {{font-weight:700; font-size:.8rem; text-align:center; padding:9px 8px;
          border:1px solid var(--lg-line); border-radius:999px;
          letter-spacing:-.02em;}}
.hrhead.corner {{border:0; background:transparent; color:var(--lg-sub);
                 font-size:.74rem; font-weight:600;}}
.hrtime {{font-size:.76rem; font-weight:600; color:var(--lg-sub); text-align:right;
          white-space:nowrap; padding:17px 10px 0 0;}}
{_TEAM_TINT}
{_DEGREE_TINT}
</style>
""", unsafe_allow_html=True)


def brand_bar(title: str, note: str = "") -> None:
    """화면 맨 위 브랜드 머리띠 — LG 마크 + 지금 보고 있는 화면 이름."""
    st.markdown(
        f'<div class="lgbar"><span class="lgmark">LG</span>'
        f'<span class="ttl">{escape(title)}</span>'
        + (f'<span class="sub">{escape(note)}</span>' if note else "")
        + "</div>",
        unsafe_allow_html=True,
    )

_TEAM_CLASS: dict[str, str] = {}


def team_colors(teams) -> None:
    """팀 이름을 가나다순으로 줄 세워 색 번호를 붙인다."""
    for index, name in enumerate(sorted({str(t).strip() for t in teams if str(t).strip()})):
        _TEAM_CLASS[name] = f"tm{index % len(TEAM_PALETTE)}"


def team_class(team) -> str:
    name = str(team or "").strip()
    if not name:
        return ""
    if name not in _TEAM_CLASS:  # 팀 목록을 못 받은 화면도 색은 갖게 한다
        _TEAM_CLASS[name] = f"tm{sum(ord(c) for c in name) % len(TEAM_PALETTE)}"
    return _TEAM_CLASS[name]


def degree_of(degree) -> tuple:
    return DEGREE_STRIPE.get(str(degree or "").strip(), DEGREE_STRIPE["미상"])


def degree_class(degree) -> str:
    return degree_of(degree)[0]


def degree_pill(degree) -> str:
    """학력 뱃지 — 학사 · 석사 · 박사를 글자로도 바로 알아보게 한다."""
    text = str(degree or "").strip()
    if not text:
        return ""
    cls = degree_of(text)[0]
    return f'<span class="hrdeg {cls}">{escape(text)}</span>'


def degree_chip(degree) -> str:
    """위젯이 들어가는 칸에도 학력 띠줄과 뱃지를 얹는다 (카드가 아니라 컨테이너일 때)."""
    _, line, _ = degree_of(degree)
    return (f'<div style="height:4px;border-radius:3px;background:{line};'
            f'margin:-4px 0 7px;"></div>{degree_pill(degree)}')


def card(top: str = "", head: str = "", sub: str = "", tone: str = "", *,
         team=None, degree=None, badge: str = "") -> str:
    """카드 한 칸 — 위(학력 뱃지 · 시간·구분) / 가운데(이름) / 아래(부가 정보).

    team 을 주면 부서 색이, degree 를 주면 학력 띠줄과 뱃지가, badge 를 주면
    우상단에 작은 표가 붙는다.
    """
    classes = " ".join(c for c in (
        "hrcard", tone, team_class(team) if team else "",
        degree_class(degree) if degree else "",
    ) if c)
    mark = f'<span class="hrbadge">{escape(str(badge))}</span>' if badge else ""
    return (
        f'<div class="{classes}">{mark}'
        f'<div class="t">{degree_pill(degree) if degree else ""}'
        f'{escape(str(top))}</div>'
        f'<div class="h">{escape(str(head))}</div>'
        f'<div class="s">{escape(str(sub))}</div></div>'
    )


def card_grid(cards: list[str], cols: int = 4) -> None:
    """카드를 칸 수가 고정된 격자로 깐다 — 모자라는 자리는 빈칸으로 채워 줄을 맞춘다."""
    if not cards:
        return
    cols = max(1, int(cols))
    pad = (-len(cards)) % cols
    body = "".join(cards) + '<div class="hrcard void"></div>' * pad
    st.markdown(f'<div class="hrgrid" style="--cols:{cols}">{body}</div>',
                unsafe_allow_html=True)


def time_grid(columns: list, slots: list, cells: dict, *, corner: str = "시간",
              heads: dict | None = None) -> None:
    """스케줄러식 시간표 — 왼쪽에 시간, 위에 팀(또는 일자·담당자)을 놓은 격자.

    cells 는 {(열 이름, 시간): 카드 HTML}. 비어 있는 칸도 빈 카드로 자리를 지켜서
    어느 팀의 몇 시 칸인지 눈으로 바로 따라갈 수 있게 한다.
    """
    if not columns or not slots:
        return
    parts = [f'<div class="hrhead corner">{escape(str(corner))}</div>']
    for name in columns:
        klass = team_class(name) if heads is None else heads.get(name, "")
        parts.append(f'<div class="hrhead {klass}">{escape(str(name))}</div>')
    for slot in slots:
        parts.append(f'<div class="hrtime">{escape(str(slot))}</div>')
        for name in columns:
            parts.append(cells.get((name, slot)) or '<div class="hrcard empty"></div>')
    st.markdown(
        f'<div class="hrsched"><div class="hrsched-in" style="--cols:{len(columns)}">'
        + "".join(parts) + "</div></div>",
        unsafe_allow_html=True,
    )


def day_title(text: str) -> None:
    st.markdown(f'<div class="hrday">{escape(text)}</div>', unsafe_allow_html=True)


# ============================================================
# 명단 전달 저장소 — HR 뷰어 ↔ 부서 뷰어가 주고받는 파일
# ============================================================
HANDOFF_DIR = PROJECT_ROOT / "data" / "handoff"


def handoff_path(rid: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", rid or "none")
    return HANDOFF_DIR / f"{safe}.json"


def load_handoff(rid: str) -> dict:
    path = handoff_path(rid)
    if not path.is_file():
        return {"round_id": rid, "teams": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"round_id": rid, "teams": {}}


def save_handoff(rid: str, doc: dict) -> None:
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    handoff_path(rid).write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def save_order(rid: str, sequence: dict[str, list[dict]],
               timing: dict | None = None) -> dict:
    """HR 가 2단계에서 잡은 면접 차례를 회차 문서에 적어 둔다.

    부서 화면은 이 순번을 보고 카드를 세우므로, 여기 적힌 순서가 곧 '인사
    담당자가 정해 보낸 초안 순서' 다.

    차례와 함께 그 차례를 만든 면접 진행 조건(시작 시각 · 한 사람당 분 · 쉬는
    시간 · 하루 인원)도 남긴다. 부서 화면과 4단계 시간표가 이 조건을 다시 읽어
    같은 시각을 가리키게 하려는 것이다 — 여기 없으면 화면마다 09:00/30분/5분
    기본값으로 제각각 그린다.
    """
    doc = load_handoff(rid)
    doc["order"] = {
        team: [
            {k: row.get(k) for k in ("applicant_id", "name", "order", "day", "time")}
            for row in rows
        ]
        for team, rows in (sequence or {}).items()
    }
    doc["order_at"] = datetime.now().isoformat(timespec="seconds")
    if timing:
        doc["timing"] = {
            "start": str(timing.get("start") or "09:00"),
            "minutes": int(timing.get("minutes") or SLOT_MINUTES),
            "rest": int(timing.get("rest") or BREAK_MINUTES),
            "per_day": int(timing.get("per_day") or SLOTS_PER_DAY),
        }
    save_handoff(rid, doc)
    return doc


def round_timing(doc: dict) -> dict:
    """그 회차의 면접 진행 조건 — 2단계에서 정한 값, 아직 없으면 기본값."""
    saved = (doc or {}).get("timing") or {}
    return {
        "start": str(saved.get("start") or "09:00"),
        "minutes": int(saved.get("minutes") or SLOT_MINUTES),
        "rest": int(saved.get("rest") or BREAK_MINUTES),
        "per_day": int(saved.get("per_day") or SLOTS_PER_DAY),
    }


def handoff_team_days(doc: dict) -> dict[str, list[str]]:
    """팀마다 며칟날까지 면접을 보는지 — 3단계에서 정해 내려보낸 값.

    옛 회차 자료에는 이 값이 없다. 그때는 그 자리에서 같은 셈(`plan_team_days`)
    으로 다시 뽑는다 — 스케줄러도 같은 함수를 쓰므로 답이 어긋나지 않는다.
    """
    saved = (doc or {}).get("team_days") or {}
    teams = (doc or {}).get("teams") or {}
    out = {team: [d for d in (saved.get(team) or (block.get("days") or []))
                  if d in SCHED_DAYS]
           for team, block in teams.items()}
    missing = {team: len((teams.get(team) or {}).get("applicants") or [])
               for team, days in out.items() if not days}
    if missing:
        # 면접일을 못 박기 전에 만든 자료 — 전체 팀 크기로 같은 셈을 다시 한다
        sizes = {team: len((block.get("applicants") or []))
                 for team, block in teams.items()}
        again = plan_team_days(sizes, SCHED_DAYS_PER_TEAM)
        for team in missing:
            out[team] = again.get(team, [])
    return out


def order_lookup(doc: dict, team: str) -> dict[str, dict]:
    """그 팀의 순번표를 '지원자 번호(없으면 이름) → 순번 정보' 로 편다."""
    out: dict[str, dict] = {}
    for row in ((doc.get("order") or {}).get(team) or []):
        key = row.get("applicant_id") or row.get("name")
        if key:
            out[str(key)] = row
    return out


def publish_handoff(rid: str, plan_id: str, applicants: list[dict],
                    interviewers: list[dict], by: str) -> dict:
    """HR 가 팀별로 '면접자 명단 + 우리 팀 담당자'를 보낸다 (기존 제출은 남긴다).

    2단계에서 잡아 둔 면접 차례가 있으면 사람마다 순번 · 일차 · 시간을 붙여
    보낸다 — 부서는 그 차례를 그대로 보고 담당자만 정하면 된다.
    """
    doc = load_handoff(rid)
    doc["round_id"] = rid
    doc["plan_id"] = plan_id
    doc["sent_at"] = datetime.now().isoformat(timespec="seconds")
    doc["sent_by"] = by
    timing = round_timing(doc)
    per_day = timing["per_day"]
    labels = slot_labels(timing["start"], per_day, timing["minutes"], timing["rest"])
    teams = doc.setdefault("teams", {})
    names = sorted({(row.get("team") or "미상") for row in applicants}
                   | {(row.get("team") or "미상") for row in interviewers})
    # 어느 팀이 며칟날까지 보는지는 **여기서 한 번** 정한다. 예전에는 인사팀이
    # 시간표를 만드는 순간에 팀 인원을 보고 뽑았는데, 부서가 몇 명을 빼면 인원이
    # 달라져 날짜까지 통째로 바뀌었다 — 부서가 '1일차' 라고 잡아 둔 자리가 매번
    # 다른 날이 되던 까닭이다. 명단과 함께 면접일도 내려보내 뒤 단계가 이 결정에
    # 종속되게 한다. 어느 팀이나 1일차부터 본다.
    sizes = {team: sum(1 for row in applicants
                       if (row.get("team") or "미상") == team) for team in names}
    # 담당자가 어느 날에 나올 수 있는지는 보지 않는다 — 우리 모델에 담당자
    # 가능 날은 없다. 가능 시간은 앞타임 · 뒤타임 · 모든타임 뿐이고, 그 덩어리는
    # 1일차든 3일차든 똑같이 적용된다.
    team_days = plan_team_days(sizes, SCHED_DAYS_PER_TEAM)
    doc["team_days"] = team_days
    for team in names:
        block = teams.setdefault(team, {})
        seq = order_lookup(doc, team)
        mine = [row for row in applicants if (row.get("team") or "미상") == team]

        def slot(row: dict) -> dict:
            """그 사람이 2단계 차례표에서 몇 번째였는지 (없으면 빈 dict)."""
            return (seq.get(str(row.get("applicant_id") or ""))
                    or seq.get(str(row.get("name") or "")) or {})

        # 2단계 차례표가 이 팀 명단을 남김없이 덮을 때만 그 차례를 그대로 쓴다.
        # 한 사람이라도 빠져 있으면 그 차례표는 지금과 다른 팀 나눔에서 만든
        # 것이다 — 빠진 사람을 뒤에 몰아 붙이면 학력 묶음이 그 자리에서 깨지므로
        # (석사가 흩어지는 원인이었다) 2단계와 같은 알고리즘으로 다시 잡는다.
        planned = bool(mine) and all(slot(row) for row in mine)
        if planned:
            ordered_rows = sorted(mine, key=lambda r: (slot(r).get("order") or 0,
                                                       r.get("name") or ""))
        else:
            ordered_rows = order_for_interview(
                [dict(row, order=None,
                      degree_full=degree_full(row.get("degree_type")))
                 for row in mine],
                balance=True, per_day=per_day,
            )
        block["applicants"] = [
            {
                "applicant_id": row["applicant_id"],
                "name": row.get("name") or row["applicant_id"],
                "degree_type": row.get("degree_type"),
                "major_final": row.get("major_final"),
                "order": number,
                "order_day": (slot(row).get("day") if planned
                              else (number - 1) // per_day + 1),
                "order_time": (slot(row).get("time") if planned
                               else labels[(number - 1) % per_day]),
            }
            for number, row in enumerate(ordered_rows, start=1)
        ]
        block["order_planned"] = planned
        # 부서 화면은 '1일차 · 2일차' 로만 자리를 잡는다. 그 팀이 며칟날까지
        # 보는지를 여기서 못 박아 내려보내야 인사팀 최종 시간표와 어긋나지 않는다.
        block["days"] = list(team_days.get(team, []))
        block["interviewers"] = [
            {
                "interviewer_id": row["interviewer_id"],
                "name": row.get("name") or row["interviewer_id"],
                "title": row.get("title") or "",
                "priority": row.get("priority"),
                "max_daily": row.get("max_daily"),
            }
            for row in interviewers if (row.get("team") or "미상") == team
        ]
        # 명단이 바뀌었으면 사라진 사람에 대한 제출은 정리한다
        live = {row["applicant_id"] for row in block["applicants"]}
        sub = block.get("submitted")
        if sub:
            sub["pairs"] = {a: i for a, i in sub["pairs"].items() if a in live}
    save_handoff(rid, doc)
    return doc


def team_only_pairs(block: dict, pairs: dict) -> dict[str, str]:
    """인사가 그 팀에 보낸 명단 안의 사람 · 그 팀 담당자로 맺은 짝만 남긴다.

    부서는 받은 목록에서 고르는 것이지 목록 밖의 사람을 넣을 수 없다. 인사가
    명단을 다시 보내 팀 구성이 바뀌면 옛 짝은 여기서 걸러진다 — 걸러 두지
    않으면 시간표가 그 사람을 못 넣어 ② 의 숫자와 어긋난다.
    """
    live = {row["applicant_id"] for row in (block.get("applicants") or [])}
    mine = {row["interviewer_id"] for row in (block.get("interviewers") or [])}
    return {a: i for a, i in (pairs or {}).items() if a in live and i in mine}


def assign_team(rid: str, team: str, pairs: dict, by: str) -> dict:
    """부서가 '배정하기' 로 정한 짝 — 아직 인사 담당자에게는 가지 않는다.

    배정과 제출을 나눈 이유는, 부서가 시간표까지 보고 고친 다음에야 인사
    담당자의 시간표에 반영되게 하기 위해서다.
    """
    doc = load_handoff(rid)
    block = doc.setdefault("teams", {}).setdefault(team, {})
    block["draft"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "by": by,
        "pairs": team_only_pairs(block, pairs),
    }
    save_handoff(rid, doc)
    return doc


def submit_team(rid: str, team: str, pairs: dict, by: str,
                seats: dict | None = None) -> dict:
    """부서가 '인사 담당자에게 보내기' 를 누른 순간 — 이때부터 5단계 시간표에 쓰인다.

    짝만이 아니라 **부서가 본 시간표의 자리** 도 함께 보낸다. 예전에는 짝만
    넘어가서 인사팀 시간표가 날 · 시각을 처음부터 새로 짰고, 그래서 부서가
    확인하고 보낸 시간표와 최종 시간표가 서로 다른 물건이 됐다. 자리는
    (몇 일차, 몇 번째 칸)으로 적는다 — 며칟날까지 볼지는 인사팀이 정한다.
    """
    doc = load_handoff(rid)
    block = doc.setdefault("teams", {}).setdefault(team, {})
    kept = team_only_pairs(block, pairs)
    block["submitted"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "by": by,
        "pairs": kept,
        "seats": {
            aid: {"day": int(seat["day"]), "slot": int(seat["slot"])}
            for aid, seat in (seats or {}).items() if aid in kept
        },
    }
    save_handoff(rid, doc)
    return doc


def team_pairs(block: dict) -> tuple[dict[str, str], bool]:
    """그 팀이 지금 보고 있는 짝 — (짝, 아직 안 보낸 배정인지)."""
    draft = (block.get("draft") or {}).get("pairs")
    sent = (block.get("submitted") or {}).get("pairs") or {}
    if draft is None:
        return dict(sent), False
    return dict(draft), dict(draft) != dict(sent)


def handoff_pairs_by_team(doc: dict) -> dict[str, dict[str, str]]:
    """팀별로 제출된 짝 {팀: {면접자 → 담당자}}.

    두 팀이 같이 보는 사람은 팀마다 담당자가 다르다. `handoff_pairs` 처럼 하나로
    합치면 나중에 읽은 팀의 짝이 앞 팀 것을 덮어써서, 한 팀은 자기가 정한
    담당자가 아닌 사람과 면접을 보게 된다 — 팀 구분을 살려 두는 쪽이 원본이다.
    """
    out: dict[str, dict[str, str]] = {}
    for team, block in (doc.get("teams") or {}).items():
        live = {row["applicant_id"] for row in (block.get("applicants") or [])}
        mine = {row["interviewer_id"] for row in (block.get("interviewers") or [])}
        out[team] = {
            applicant: interviewer
            for applicant, interviewer in (
                ((block.get("submitted") or {}).get("pairs") or {}).items()
            )
            if applicant in live and interviewer in mine
        }
    return out


def handoff_seats_by_team(doc: dict) -> dict[str, dict[str, dict]]:
    """팀별로 제출된 자리 {팀: {면접자: {"day": 일차, "slot": 칸 번호}}}.

    짝이 살아 있는 사람의 자리만 넘긴다 — 짝이 지워졌는데 자리만 남으면 최종
    시간표가 담당자 없는 칸을 붙잡고 있게 된다.
    """
    out: dict[str, dict[str, dict]] = {}
    for team, mine in handoff_pairs_by_team(doc).items():
        block = (doc.get("teams") or {}).get(team) or {}
        seats = (block.get("submitted") or {}).get("seats") or {}
        kept = {
            aid: {"day": int(seat.get("day", 0)), "slot": int(seat.get("slot", -1))}
            for aid, seat in seats.items()
            if aid in mine and int(seat.get("day", 0)) >= 1 and int(seat.get("slot", -1)) >= 0
        }
        if kept:
            out[team] = kept
    return out


def handoff_pairs(doc: dict) -> dict[str, str]:
    """모든 팀의 제출을 하나로 합친다 (면접자 → 면접 담당자).

    같이 보는 사람은 여기서 한 팀 것만 남으므로 담당자를 가리는 자리에는
    `handoff_pairs_by_team` 을 쓴다. 이 함수는 '짝이 지어졌는가' 만 볼 때 쓴다.

    보낸 것만 센다 — 배정만 해 두고 아직 안 보낸 팀은 인사 담당자 시간표에
    들어가지 않는다.

    그리고 그 팀에 **보낸 명단 안의 사람** 과 **그 팀 담당자** 로 맺은 짝만
    인정한다. 인사가 명단을 다시 보내면 팀 구성이 바뀌는데, 그때 남아 있던 옛
    제출까지 세어 버리면 ② 의 '면접 못 보는 사람' 은 줄었는데 시간표는 그 사람을
    넣지 못해 두 숫자가 어긋난다 — 각 단계의 명단에 종속시키려는 것이다.
    """
    out: dict[str, str] = {}
    for block in (doc.get("teams") or {}).values():
        live = {row["applicant_id"] for row in (block.get("applicants") or [])}
        mine = {row["interviewer_id"] for row in (block.get("interviewers") or [])}
        for applicant, interviewer in (
            ((block.get("submitted") or {}).get("pairs") or {}).items()
        ):
            if applicant in live and interviewer in mine:
                out[applicant] = interviewer
    return out


# ============================================================
# 공통 HTTP 헬퍼
# ============================================================
@st.cache_resource
def http() -> httpx.Client:
    """연결을 재사용하는 공용 클라이언트 (매번 새로 연결하면 왕복이 더 든다)."""
    return httpx.Client(
        timeout=30.0,
        limits=httpx.Limits(max_keepalive_connections=16, max_connections=32),
    )


def unwrap(response: httpx.Response):
    """공통 응답 봉투 {"data": ..., "error": ...} 에서 data 를 꺼낸다."""
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def error_text(response: httpx.Response) -> str:
    """실패 응답에서 사람이 읽을 메시지를 뽑는다."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return f"{err.get('code')}: {err.get('message')}"
        if "detail" in body:
            return str(body["detail"])[:300]
    return str(body)[:300]


@st.cache_data(ttl=120, show_spinner=False)
def fetch_json(url: str, params: tuple = ()):
    """GET 후 봉투를 벗겨 반환. (data, error_message)"""
    try:
        r = http().get(url, params=dict(params), timeout=20.0)
    except Exception as e:  # 서비스 미기동 등
        return None, str(e)
    if r.status_code != 200:
        return None, f"status {r.status_code}: {error_text(r)}"
    return unwrap(r), None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_bytes(url: str):
    """파일 다운로드용 — download_button 이 데이터를 미리 요구해서 캐시해 둔다."""
    try:
        r = http().get(url, timeout=60.0)
    except Exception as e:
        return None, str(e)
    if r.status_code != 200:
        return None, f"status {r.status_code}: {error_text(r)}"
    return r.content, None


def post_json(url: str, payload: dict, timeout: float = 60.0):
    """POST(JSON) 후 (data, error_message). 예외도 문자열로 접어서 돌려준다."""
    try:
        r = http().post(url, json=payload, timeout=timeout)
    except Exception as e:
        return None, str(e)
    if r.status_code >= 300:
        return None, f"status {r.status_code}: {error_text(r)}"
    return unwrap(r), None


def put_json(url: str, payload: dict, timeout: float = 60.0):
    try:
        r = http().put(url, json=payload, timeout=timeout)
    except Exception as e:
        return None, str(e)
    if r.status_code >= 300:
        return None, f"status {r.status_code}: {error_text(r)}"
    return unwrap(r), None


def clear_caches() -> None:
    fetch_json.clear()
    fetch_bytes.clear()
    fetch_round_ids.clear()
    fetch_rounds.clear()


@st.cache_data(ttl=60, show_spinner=False)
def fetch_round_ids():
    """감사 로그에 남은 Round 목록(최근 순). ID 를 타이핑하지 않게 하기 위한 것."""
    # 07 은 시간 오름차순으로 자르므로, 최근 회차를 놓치지 않게 상한(5000)까지 받는다
    try:
        r = http().post(f"{AUDIT}/api/v1/audit/query", json={"limit": 5000}, timeout=15.0)
    except Exception:
        return []
    if r.status_code != 200:
        return []
    latest: dict[str, str] = {}
    for ev in (unwrap(r) or {}).get("events", []):
        rid, ts = ev.get("round_id"), ev.get("timestamp") or ""
        if rid:
            latest[rid] = max(latest.get(rid, ""), ts)
    return [rid for rid, _ in sorted(latest.items(), key=lambda kv: kv[1], reverse=True)]


@st.cache_data(ttl=60, show_spinner=False)
def fetch_rounds(round_id: str = ""):
    """그 회차에 남아 있는 시간표 목록 (최신순).

    예전에는 감사 로그의 '시간표 만듦' 기록만 보고 골랐는데, 기록은 남아 있어도
    시간표가 지워졌거나 DB 를 갈아 끼우면 '스케줄을 찾을 수 없습니다' 만 나왔다.
    그래서 04(스케줄러)에게 실제 목록을 먼저 묻고, 04 가 답하지 못할 때만
    예전처럼 감사 로그로 되돌아간다.
    """
    if round_id:
        try:
            r = http().get(f"{SCHEDULER}/api/v1/schedules/rounds/{round_id}", timeout=15.0)
            rows = unwrap(r) if r.status_code == 200 else None
        except Exception:
            rows = None
        if rows:
            return [
                {
                    "round_id": row.get("round_id") or round_id,
                    "schedule_id": row.get("schedule_id"),
                    "at": (row.get("generated_at") or "")[:16].replace("T", " "),
                    "assigned": row.get("total_assigned"),
                    # 확정했는지 — 4단계 안내가 마지막 칸을 접을 때 본다
                    "status": row.get("status") or "",
                }
                for row in rows
                if row.get("schedule_id")
            ]
    try:
        r = http().post(
            f"{AUDIT}/api/v1/audit/query",
            json={"event_types": ["SCHEDULE_GENERATED"], "limit": 5000},
            timeout=15.0,
        )
    except Exception:
        return []
    if r.status_code != 200:
        return []
    events = (unwrap(r) or {}).get("events", [])
    seen, out = set(), []
    for ev in sorted(events, key=lambda e: e.get("timestamp") or "", reverse=True):
        rid = ev.get("round_id")
        sid = (ev.get("payload") or {}).get("schedule_id")
        if not rid or not sid or rid in seen:
            continue
        seen.add(rid)
        out.append({
            "round_id": rid,
            "schedule_id": sid,
            "at": (ev.get("timestamp") or "")[:16].replace("T", " "),
            "assigned": (ev.get("payload") or {}).get("total_assigned"),
            "status": "",   # 감사 로그로 되돌아온 길에는 확정 여부가 없다
        })
    return out


def round_selector(key: str, label: str = "Round"):
    """최근 Round 를 고르는 셀렉트박스. 목록이 비면 직접 입력으로 넘어간다."""
    ids = fetch_round_ids()
    last = st.session_state.get("round_id")
    if not ids:
        return st.text_input(f"{label} ID", value=last or "", key=f"{key}_manual")
    idx = ids.index(last) if last in ids else 0
    col_sel, col_btn = st.columns([5, 1])
    picked = col_sel.selectbox(label, ids, index=idx, key=key)
    col_btn.write("")
    if col_btn.button("🔄", key=f"{key}_refresh", help="Round 목록 새로고침"):
        clear_caches()
        st.rerun()
    return picked


# ============================================================
# 표에 나가는 말 — 서비스가 주는 영어 키를 고객이 읽는 우리말로 바꾼다
# ============================================================
COLUMN_LABELS = {
    "applicant_id": "지원자 번호", "applicant_name": "지원자", "name": "성명",
    "team": "팀", "team_name": "팀", "org": "조직", "email": "이메일",
    "degree": "학력", "degree_type": "학력", "major_final": "전공",
    "job_role": "직무", "priority": "역할", "max_daily": "하루 최대",
    "title": "직급",
    "day": "일차", "hour": "시간", "slot_count": "가능 시간 수",
    "interviewer_name": "면접 담당자", "file_name": "파일 이름",
    "applicant_count": "인원", "row_count": "줄 수", "created_at": "만든 시각",
    "updated_at": "고친 시각", "actor": "담당자", "kind": "종류",
    "is_active": "현재 사용", "reason_tags": "배정 사유", "lock_level": "확정 여부",
    "responded": "회신", "invited": "요청", "total_slots": "가능 시간 합계",
    "target_headcount": "정원", "primary_job": "주력 직무",
    "secondary_job": "보조 직무", "preferred_majors": "선호 전공",
    "org_allowed": "받는 조직", "grad_ratio_target": "대학원 비율",
    "special_tags": "특수 조건", "status": "상태", "phase": "단계",
    "sent_count": "발송", "subject": "제목", "to": "받는 사람",
}

# 고객 화면에 나가면 안 되는 내부 식별자들
HIDDEN_COLUMNS = {
    "version_id", "plan_id", "schedule_id", "request_id", "assignment_id",
    "event_id", "correlation_id", "response_id", "notification_id",
    "fingerprint", "storage_path", "file_path", "round_id", "created_by",
    "master_version_id", "base_version_id", "source_version_ids", "payload",
    "interviewer_id", "profile_id", "idempotency_key",
}


# 배정 사유 — 서비스는 영어 태그로 주지만 고객 화면에는 우리말로만 나간다
TAG_LABELS = {
    "PRIMARY_JOB": "팀 주력 직무 매칭",
    "SECONDARY_JOB": "팀 보조 직무 매칭",
    "PREFERRED_MAJOR": "팀 선호 전공 매칭",
    "ORG_MAIN": "제1기술원 지원자",
    "ORG_ALT_QUOTA": "받아 주는 조직 쿼터",
    "TARGET_LAB": "타겟랩 출신",
    "ADVISOR_ROUTE": "지도교수 연계",
    "GRAD_BALANCE": "학력 비율 맞추려고 옮김",
    "OVERFLOW_REASSIGN": "정원이 차서 차순위 팀으로",
    "DUPLICATE_REVIEW": "두 팀이 함께 볼 사람",
    "HR_MANUAL": "인사 담당자 재량",
    "TEAM_INHERITED": "1단계에서 팀이 적어 낸 사람",
    "ORG_UNMATCHED": "팀 조건과 안 맞지만 팀이 지목",
    # 부서가 보낸 자리를 최종 시간표가 어떻게 다뤘는지
    "DEPT_SEAT": "부서가 잡아 둔 자리 그대로",
    "SEAT_MOVED_TAKEN": "부서 자리가 이미 차서 옮김",
    "SEAT_MOVED_BAND": "부서 자리가 담당자 가능 시간 밖이라 옮김",
    "SEAT_MOVED_CAP": "담당자 하루 한도가 차서 옮김",
    "SEAT_MOVED_BUSY": "담당자가 그 시각에 다른 면접이 있어 옮김",
    "SEAT_MOVED_OWNER": "그 자리 담당자를 찾을 수 없어 옮김",
    "SEAT_MOVED_DAY": "부서가 적은 일차가 이 팀 면접일 수보다 커서 옮김",
}


def tag_label(tag: str) -> str:
    """영어 태그 하나를 우리말로 — 모르는 태그는 그대로 둔다."""
    return TAG_LABELS.get(str(tag), str(tag))


def tag_text(tags) -> str:
    """배정 사유 태그를 우리말 한 줄로 — 모르는 태그는 그대로 둔다."""
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,·]", tags) if t.strip()]
    if tags is None or isinstance(tags, float):
        return ""
    return " · ".join(tag_label(t) for t in tags)


def ko_frame(rows, keep=None, drop_ids: bool = True) -> pd.DataFrame:
    """영어 키를 우리말 컬럼으로 바꾸고 내부 식별자는 걷어낸 표를 만든다."""
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows or [])
    if frame.empty:
        return frame
    if keep:
        frame = frame[[c for c in keep if c in frame.columns]]
    elif drop_ids:
        frame = frame[[c for c in frame.columns if c not in HIDDEN_COLUMNS]]
    if "reason_tags" in frame.columns:
        frame["reason_tags"] = frame["reason_tags"].map(tag_text)
    if "lock_level" in frame.columns:   # DRAFT / CONFIRMED / LOCKED 도 우리말로
        frame["lock_level"] = frame["lock_level"].map(
            lambda v: say(v, STATUS_LABELS)
        )
    return frame.rename(columns=COLUMN_LABELS)


def role_label(priority) -> str:
    return "팀장" if priority == 1 else "실무"


def iv_label(row: dict, fallback: str = "") -> str:
    """담당자를 '성명 직급' 으로 적는다 — 이름만으로는 누군지 헷갈린다."""
    name = str(row.get("name") or "").strip() or fallback \
        or str(row.get("interviewer_id") or "")
    title = str(row.get("title") or "").strip()
    return f"{name} {title}".strip()


def iv_names(rows) -> dict:
    """사번 → '성명 직급' 표 — 화면 어디서나 같은 이름으로 보이게 한다."""
    return {row["interviewer_id"]: iv_label(row, row["interviewer_id"])
            for row in (rows or [])}


# 담당자가 하루 중 언제 들어갈 수 있는지 — 칸을 하나씩 고르기는 번거로우니
# 앞타임 · 뒤타임 두 덩어리로만 받는다. 어느 칸이 어느 덩어리인지는 3단계에서
# 정하는 면접 진행 조건이 정하므로, 시각을 여기 박아 두지 않는다.
#
# 두 덩어리는 일부러 겹쳐 둔다 — 앞타임은 14시까지, 뒤타임은 12시부터. 예전처럼
# 오전 · 오후로 갈라 받으면 점심에 걸치는 칸을 어느 쪽도 못 맡아 그 시간이
# 통째로 비었다.
BAND_UNSET = "아직 안 정함"
BAND_CHOICES = [BAND_ALL, BAND_FRONT, BAND_BACK, BAND_NONE]


def band_of(row: dict, timing: dict | None = None) -> str:
    """저장된 가용성을 앞타임 · 뒤타임 덩어리로 되읽는다."""
    band = band_name(str(row.get("time_band") or "").strip())
    if band in BAND_CHOICES:
        return band
    hours = {h for hs in (row.get("availability") or {}).values() for h in hs}
    if not hours:
        return BAND_UNSET
    return contract_band_of(row.get("availability"), timing)


def band_cap(band: str, timing: dict | None = None) -> int:
    """그 덩어리로 하루에 볼 수 있는 최대 인원."""
    if band in (BAND_UNSET, ""):
        return len(SCHED_HOURS)
    return len(band_hours(band, timing))


def iv_availability(row: dict) -> dict[str, list[str]]:
    """이 담당자가 적어 낸 가능 시간 — 날은 지우고, 칸 이름은 지금 것으로.

    `normalize_availability()` 가 둘 다 해 준다. 저장된 값에는 옛 표기('09시')와
    날별로 갈라 둔 옛 자료가 남아 있는데, 스케줄러는 읽을 때마다 이걸로 맞춰 보므로
    화면도 같은 함수를 써야 한다. 화면만 날것으로 비교하면 실제로는 되는 분이
    화면에서만 '되는 칸이 하나도 없는 사람' 이 된다.
    """
    return normalize_availability(row.get("availability") or {})


def iv_answered(row: dict) -> bool:
    """이 담당자가 가능 시간을 적어 냈는가.

    안 적어 낸 사람을 '아무 때나 되는 사람' 으로 보면 안 된다. 부서 화면은
    그렇게 보고 자리를 맡겼는데 인사팀 스케줄러는 '되는 시간이 하나도 없는
    사람' 으로 읽어 그 자리를 통째로 버렸다 — 부서가 확인하고 보낸 시간표와
    최종 시간표가 서로 다른 물건이 되던 가장 큰 까닭이다.
    """
    return any(iv_availability(row).values())


def iv_open_slots(row: dict, days: list[str], per_day: int,
                  ignore: bool = False) -> dict[int, set[int]]:
    """담당자가 일차마다 맡을 수 있는 자리 번호 {일차(0부터): {칸 번호}}.

    가능 시간은 어느 날이든 같은 덩어리(앞타임 · 뒤타임 · 모든타임)라서 어느
    일차든 맡을 수 있는 칸이 같다. 그래도 일차별로 돌려주는 까닭은 부르는 쪽이
    (일차, 칸) 으로 자리를 세기 때문이다. 자리 번호는 스케줄러의 칸 번호와 같아야
    최종 시간표가 같은 자리를 가리킨다.
    """
    every = set(range(min(per_day, len(SCHED_HOURS))))
    if ignore:
        return {index: set(every) for index in range(len(days))}
    hours = {h for day_hours in iv_availability(row).values() for h in day_hours}
    mine = {slot for slot in every if SCHED_HOURS[slot] in hours}
    return {index: set(mine) for index in range(len(days))}


def team_open_slots(rows: list[dict], days: list[str], per_day: int,
                    ignore: bool = False) -> tuple[dict[str, dict[int, set[int]]], list[str]]:
    """팀 담당자들이 맡을 수 있는 자리와, 아직 가능 시간을 안 적어 낸 사람들."""
    can = {row["interviewer_id"]: iv_open_slots(row, days, per_day, ignore)
           for row in rows or []}
    unanswered = [row["interviewer_id"] for row in rows or []
                  if not iv_answered(row)]
    return can, unanswered


KIND_LABELS = {"master": "전체 지원자 명단", "team_distribution": "팀별 명단"}
STATUS_LABELS = {
    "DRAFT": "작성 중", "ADJUSTED": "조정됨", "APPROVED": "승인 완료",
    "CONFIRMED": "확인됨",
    "REJECTED": "반려", "READY": "준비됨", "LOCKED": "확정", "OK": "이상 없음",
    "GENERATED": "생성 완료", "PENDING": "대기", "SENT": "발송 완료",
}


def say(value, table: dict) -> str:
    """코드 값을 우리말로 — 모르는 값은 그대로 보여 준다.

    서비스마다 상태를 approved 로도, APPROVED 로도 돌려주므로 대소문자는 가린다.
    """
    text = str(value or "").strip()
    if not text:
        return "-"
    for key in (text, text.upper(), text.lower()):
        if key in table:
            return table[key]
    return text


# ============================================================
# 사이드바 — 하는 일 고르기 · 면접 회차
# ============================================================
VIEWERS = [
    "인사 담당자",
    "현업 부서 (면접관)",
    "시스템 관리자",
]
VIEWER_ICONS = ["🧑‍💼", "🏢", "⚙️"]
VIEWER_HINTS = [
    "명단을 모으고 면접 일정을 만듭니다",
    "우리 팀 면접 담당자를 정하고, 받은 명단에서 면접 볼 사람을 고릅니다",
    "서비스 상태와 기록을 봅니다",
]

# 인사 담당자가 하는 일 (사람 담당자를 고르고 짝 맞추는 일은 현업 부서 쪽으로 뺐다)
MENUS = [
    "지원자 명단 받기",
    "팀별 명단 나누기",
    "부서에 명단 보내기",
    "면접 시간표 만들기",
]
MENU_HINTS = [
    "받은 엑셀 파일들을 하나로 합쳐 이번 회차의 지원자 명단을 확정합니다.",
    "확정된 명단을 팀별로 나누고 면접 순서를 잡습니다.",
    "각 팀에 면접자 명단을 보내고, 담당자들에게 가능한 시간을 물어봅니다.",
    "전체 면접 시간표를 만들고 확인합니다.",
]

# 현업 부서(면접관)가 하는 일
DEPT_MENUS = [
    "우리 팀 면접 담당자 정하기",
    "면접자 담당자 매칭",
]
DEPT_HINTS = [
    "우리 팀에서 면접에 들어갈 사람을 등록하고, 이번 회차에 들어갈 사람만 고릅니다.",
    "받은 명단에서 면접 볼 사람과 담당자를 짝지어 배정하고, 확인한 뒤 인사 담당자에게 보냅니다.",
]

# 두 화면이 번갈아 진행되므로, 어느 차례인지 왼쪽에 적어 준다
FLOW_NOTE = (
    "인사 ①② → 부서 ① 담당자 정하기 → 인사 ③ 명단 보내기 → "
    "부서 ② 면접자 담당자 매칭 → 인사 ④ 시간표"
)

# 왼쪽 메뉴 '?' 에 올려 두면 나오는 설명. 처음 다루는 사람은 어느 화면에서 무엇을
# 눌러야 하는지부터 막히므로, 단계 이름이 아니라 '누를 버튼' 으로 적는다.
HR_HELP_STEPS = [
    ("1. 지원자 명단 받기",
     "엑셀을 올리고 **명단 합치기** → 파일마다 값이 다른 사람을 골라 준 뒤 "
     "**이 명단으로 확정** 까지 누릅니다."),
    ("2. 팀별 명단 나누기",
     "**팀별로 나누기** 를 누르면 팀이 정해집니다. 팀 인원이 넘치거나 모자라면 "
     "그 자리에서 옮기고 **이 배정안으로 확정** 을 누릅니다."),
    ("3. 부서에 명단 보내기",
     "면접 시각 조건을 정하고 **부서에 명단 보내기** 를 누릅니다. 이때 팀마다 "
     "며칟날까지 볼지가 정해지고 — 어느 팀이나 1일차부터 — 뒤 단계는 이 날들을 "
     "따라갑니다."),
    ("4. 면접 시간표 만들기",
     "부서가 회신을 마친 뒤 **시간표 만들기** 를 누릅니다. 부서가 잡아 둔 자리는 "
     "그대로 쓰고 어긋난 자리만 옮깁니다. 두 팀에 겹쳐 잡힌 사람은 "
     "**오류 수정하기** 로 다시 앉힙니다."),
]
DEPT_HELP_STEPS = [
    ("1. 우리 팀 면접 담당자 정하기",
     "면접에 들어갈 사람을 등록하고, 각자 **가능한 시간**(앞타임 · 뒤타임 · "
     "모든타임)을 적은 뒤 이번 회차에 들어갈 사람만 골라 둡니다."),
    ("2. 면접자 담당자 매칭",
     "인사가 보낸 명단에서 면접 볼 사람을 고르고 담당자를 정합니다. "
     "**자동 배정** 은 담당자가 가능하다고 한 시간 안에서만 자리를 "
     "잡습니다. 우리 팀 시간표를 확인한 뒤 **인사 담당자에게 보내기** 를 "
     "누릅니다."),
]
# 말풍선은 300px 까지만 펼쳐지고 그 뒤는 밀려 들어가므로, 꼬리말은 한 줄로 둔다.
# 번갈아 진행되는 차례는 바로 아래 `FLOW_NOTE` 가 늘 보여 주고 있어 겹쳐 적지 않는다.
HELP_TAIL = "앞 단계를 다시 하면 뒤 단계는 비워집니다."


def help_text(steps: list[tuple[str, str]], tail: str = HELP_TAIL) -> str:
    """'?' 에 올려 두면 나오는 글 — 단계 이름을 굵게 하고 한 줄씩 띄운다."""
    body = "\n\n".join(f"**{name}**  \n{text}" for name, text in steps)
    return f"{body}\n\n{tail}"


def side_title(text: str, steps: list[tuple[str, str]]) -> None:
    """왼쪽 메뉴 제목 + 같은 줄의 '?' 도움말.

    말풍선은 **스트림릿이 화면 맨 위에 따로 그려 준다.** 직접 그려 봤더니 왼쪽
    메뉴 칸이 넘치는 것을 잘라 내서 글이 반쯤 잘린 채로 나왔다 — 옆으로 띄우면
    좌우가 잘리고, 아래로 펴면 메뉴 폭에 눌려 700px 넘게 길어져 아래가 잘린다.
    화면 맨 위에 그리면 자리가 모자랄 때 알아서 뒤집히고 잘리지 않는다.
    """
    st.markdown(f"**{text}**", help=help_text(steps))


def render_step_guide(rows: list[tuple[bool, str, str]]) -> None:
    """이 화면에서 지금 눌러야 하는 버튼 — 끝난 단계는 초록으로 접어 둔다.

    rows: (끝났는가, 단계 번호, 할 일). 진행에 따라 문구가 바뀌므로 화면마다
    지금 상태에서 만들어 넘긴다.
    """
    if not rows:
        return
    st.markdown(
        "".join(
            f'<div class="lgstep {"done" if done else ""}">'
            f'<span class="n">{escape(step)}</span><span>{text}</span></div>'
            for done, step, text in rows
        ),
        unsafe_allow_html=True,
    )

st.session_state.setdefault("round_input", time.strftime("R%Y%m%d-01"))
st.session_state.setdefault("actor", "hr_console")
st.session_state.setdefault("viewer", VIEWERS[0])
st.session_state.setdefault("menu", MENUS[0])
st.session_state.setdefault("dept_menu", DEPT_MENUS[0])


def nav_button(label: str, key: str, active: bool, slot: str, value: str) -> None:
    """라디오 대신 누르는 버튼 — 지금 보고 있는 곳은 진하게 칠한다."""
    if st.button(label, key=key, width="stretch",
                 type="primary" if active else "secondary"):
        st.session_state[slot] = value
        st.rerun()


with st.sidebar:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;margin:2px 0 18px;">'
        f'<span class="lgmark">LG</span>'
        f'<span style="font-weight:700;font-size:1.02rem;letter-spacing:-.02em;">'
        f'면접 진행 도우미</span></div>',
        unsafe_allow_html=True,
    )
    viewer = st.session_state["viewer"]
    menu = st.session_state["menu"]
    dept_menu = st.session_state["dept_menu"]

    st.markdown("**어떤 일을 하시나요?**")
    for index, name in enumerate(VIEWERS):
        nav_button(f"{VIEWER_ICONS[index]}  {name}", f"nav_view_{index}",
                   viewer == name, "viewer", name)
    st.caption(VIEWER_HINTS[VIEWERS.index(viewer)] if viewer in VIEWERS else "")

    if viewer == VIEWERS[0]:
        st.divider()
        side_title("진행 순서", HR_HELP_STEPS)
        for index, name in enumerate(MENUS):
            nav_button(f"{index + 1}. {name}", f"nav_menu_{index}",
                       menu == name, "menu", name)
        st.caption(FLOW_NOTE)
    elif viewer == VIEWERS[1]:
        st.divider()
        side_title("우리 팀이 할 일", DEPT_HELP_STEPS)
        for index, name in enumerate(DEPT_MENUS):
            nav_button(f"{index + 1}. {name}", f"nav_dept_{index}",
                       dept_menu == name, "dept_menu", name)
        st.caption(FLOW_NOTE)

    st.divider()
    st.markdown("**면접 회차**")
    recent = fetch_round_ids()
    if recent:
        picked = st.selectbox(
            "지난 회차 불러오기", ["(새로 입력)"] + recent, key="round_pick",
            label_visibility="collapsed",
        )
        # 셀렉트박스로 고른 값을 아래 text_input 의 기본값으로 밀어 넣는다.
        # (위젯 생성 전에 세션 값을 바꿔야 Streamlit 이 예외를 내지 않는다)
        if picked != "(새로 입력)" and picked != st.session_state.get("_round_pick_last"):
            st.session_state["_round_pick_last"] = picked
            st.session_state["round_input"] = picked
    round_id = st.text_input(
        "회차 이름", key="round_input",
        help="이번 채용 면접을 부르는 이름입니다. 예: R20260729-01",
    ).strip()
    st.session_state["round_id"] = round_id
    actor = st.text_input(
        "내 이름", key="actor", help="누가 한 일인지 기록에 남습니다.",
    ).strip() or "hr_console"

    st.divider()
    if st.button("🔄 화면 새로 고침", width="stretch", key="nav_refresh"):
        clear_caches()
        st.rerun()


def round_plan_id() -> str:
    """이번 회차의 배정안 번호 — 화면이 잊었으면 서버에서 되찾아 온다.

    배정안 번호는 브라우저 세션에만 있어서, 콘솔을 새로 열거나 다른 사람이
    이어받으면 4단계에서 '2단계에서 팀별 명단을 먼저 나눠 주세요' 만 보게 된다.
    회차 이름은 늘 있으니 그것으로 가장 최근 배정안을 찾아 다시 채운다.
    """
    current = st.session_state.get("plan_id") or ""
    if current or not round_id:
        return current
    plans, err = fetch_json(f"{DISTRIBUTOR}/api/v1/distribute/rounds/{round_id}/plans")
    if err or not plans:
        return ""
    found = str(plans[0].get("plan_id") or "")
    if found:
        st.session_state["plan_id"] = found
    return found


def plan_field(key: str, host=None) -> str:
    """이번 회차의 팀 배정안을 그대로 쓴다 — 화면에 번호를 내걸지 않는다.

    2단계에서 배정안을 만들면 값이 자동으로 따라오므로 고객은 아무것도 입력하지
    않아도 된다. 지난 배정안을 다시 불러야 하는 드문 경우에만 '자세히' 안에서
    번호를 직접 넣는다.
    """
    current = round_plan_id()
    seen = st.session_state.get(f"{key}_src")
    if current and current != seen and st.session_state.get(key, "") in ("", seen or ""):
        st.session_state[key] = current
    st.session_state[f"{key}_src"] = current
    with (host or st).expander("다른 배정안 쓰기 (거의 쓸 일 없습니다)"):
        st.text_input(
            "배정안 번호", key=key,
            help="비워 두면 이번 회차에서 만든 배정안을 그대로 씁니다.",
        )
    return (st.session_state.get(key) or "").strip()


def need_round() -> bool:
    if not round_id:
        st.warning("왼쪽에서 '면접 회차' 이름을 먼저 적어 주세요.")
        return False
    return True


# ============================================================
# 1. 자료취합 버전관리
# ============================================================
_TEAM_FILE = re.compile(r"희망지원자[_\-\s]*(?P<team>.+)$")
KIND_MASTER = "master"
KIND_TEAM = "team_distribution"


# 한 회차의 작업 결과 — 파일을 다시 올리거나 Round 를 바꾸면 전부 무효가 된다
ROUND_STATE_KEYS = (
    "v_compare_pick", "v_compare_result", "v_selections", "v_registered",
    "v_auto_key", "v_auto_done", "merged_version", "master_version_id",
    "plan_id", "plan_summary", "d_plan_id", "roster_table", "roster_days",
    "roster_matrix", "schedule_id", "s_manual", "tv_done",
    "c_plan", "s_plan", "d_plan_id_src", "c_plan_src", "s_plan_src",
)


def reset_round_state(keep: tuple[str, ...] = ()) -> None:
    """이 회차의 화면 상태를 버린다.

    `keep` 은 지금 화면에 이미 그려진 입력칸을 위한 것이다 — 그린 뒤에 그 값을
    지우면 Streamlit 이 다음 줄에서 놀란다.
    """
    for key in ROUND_STATE_KEYS:
        if key not in keep:
            st.session_state.pop(key, None)


def delete_json(url: str, timeout: float = 60.0):
    try:
        r = http().delete(url, timeout=timeout)
    except Exception as e:
        return None, str(e)
    if r.status_code >= 300:
        return None, f"status {r.status_code}: {error_text(r)}"
    return unwrap(r), None


def reset_round_downstream(rid: str, keep: tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """1단계 뒤의 모든 것을 지운다 — 배포안 · 회신 · 시간표 · 부서에 보낸 명단.

    지원자 명단이 바뀌면 그 명단으로 만든 뒤 단계는 전부 무효다. 그런데 화면
    상태만 지우면 서버에는 그대로 남아, 2단계가 옛 배포안을 되찾아 오고
    (`round_plan_id`) ③ 은 회차 목록의 최신 시간표를 '지금 상태' 로 집어 든다.
    지운 명단으로 계속 진행하게 되는 것이다.

    면접관 마스터 명단은 회차와 무관하므로 건드리지 않는다.
    """
    done, failed = [], []
    steps = (
        ("배포안", f"{DISTRIBUTOR}/api/v1/distribute/rounds/{rid}",
         ("deleted_plans", "개")),
        # 회신이 0건이어도 보낸 조사 자체는 지워야 하므로 사람 수로 알린다
        ("면접 가능 시간 조사", f"{COLLECTOR}/api/v1/rounds/{rid}",
         ("deleted_invitees", "명분")),
        ("시간표", f"{SCHEDULER}/api/v1/schedules/rounds/{rid}",
         ("deleted_schedules", "개")),
        ("자리 다시 잡기 기록", f"{REPAIR_ENGINE}/api/v1/repair/rounds/{rid}",
         ("deleted_events", "건")),
    )
    for label, url, (field, unit) in steps:
        data, err = delete_json(url)
        if err:
            failed.append(f"{label}({err})")
        else:
            done.append(f"{label} {(data or {}).get(field, 0)}{unit}")

    path = handoff_path(rid)
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            failed.append(f"부서에 보낸 명단({exc})")
        else:
            done.append("부서에 보낸 명단")

    reset_round_state(keep)
    clear_caches()
    return done, failed


def report_reset(done: list[str], failed: list[str]) -> None:
    if done:
        st.info("뒤 단계를 비웠습니다 — " + " · ".join(done))
    if failed:
        st.warning(
            "다음은 지우지 못했습니다 — " + " · ".join(failed)
            + ". 해당 서비스가 떠 있는지 확인해 주세요."
        )


def sync_round() -> None:
    """Round 가 바뀌면 이전 회차의 대조·배포 결과를 전부 버린다.

    1번(등록·대조)과 2번(명단 정리) 어느 쪽으로 들어와도 같은 판단을 해야 하므로
    두 화면 모두 이 함수를 먼저 부른다.
    """
    if st.session_state.get("v_round") != round_id:
        st.session_state["v_round"] = round_id
        reset_round_state()


def classify_local(file_name: str) -> tuple[str, str]:
    """서버(01 merge_service.classify_file)와 같은 규칙 — 업로드 직후 미리 보여준다."""
    stem = Path(file_name or "").stem.strip()
    match = _TEAM_FILE.search(stem)
    if match:
        return KIND_TEAM, (match.group("team").strip(" _-") or "미상")
    return KIND_MASTER, ""


def render_versions() -> None:
    st.header("1단계 · 지원자 명단 받기")
    st.caption(
        "부서에서 받은 엑셀 파일을 한꺼번에 올려 주세요. 같은 지원자의 값이 파일마다 "
        "다르면 찾아서 알려 드리고, 어느 쪽을 쓸지 고르면 이번 회차의 확정 명단이 "
        "만들어집니다."
    )
    if not need_round():
        return

    sync_round()

    # 처음 다루는 사람은 어느 버튼부터 눌러야 하는지에서 막힌다 — 지금 상태에서
    # 다음에 누를 것을 화면 맨 위에 적어 둔다.
    files_now = (fetch_json(f"{VERSION_MANAGER}/api/v1/versions/{round_id}/history")[0]
                 or [])
    render_step_guide([
        (bool(files_now), "①",
         "엑셀 파일을 올리고 <b>올리기</b> 를 누릅니다."
         if not files_now else f"엑셀 {len(files_now)}개를 올렸습니다."),
        (bool(files_now), "②③",
         "맞춰 볼 파일을 고르고 <b>맞춰 보기</b> — 값이 다른 사람은 어느 쪽을 쓸지 "
         "고릅니다."),
        (bool(st.session_state.get("master_version_id")), "④",
         "<b>확정 명단 만들기</b> 를 누르면 이번 회차 명단이 정해집니다. "
         "그다음 <b>2. 팀별 명단 나누기</b> 로 갑니다."),
    ])

    # ---------------- 업로드 · 등록 ----------------
    st.subheader("① 엑셀 파일 올리기")
    st.caption(
        "여기 올린 파일이 이번 회차의 전부가 됩니다. 올리기 → 맞춰 보기 → 확정까지가 "
        "한 묶음이고, 2단계는 그 결과만 가지고 팀별로 나눕니다. "
        "**명단을 새로 올리거나 지우면 2단계 이후(팀 나눔 · 부서 회신 · 시간표 · "
        "부서에 보낸 명단)는 함께 비워집니다** — 지난 명단으로 만든 결과가 남아 "
        "있으면 그것이 계속 따라가기 때문입니다."
    )
    r1, r2 = st.columns([3, 2])
    replace = r1.checkbox(
        "올릴 때 이번 회차에 먼저 올린 파일 지우기", value=True, key="v_reset",
        help="같은 회차를 다시 올릴 때 예전 파일이 섞이지 않게 합니다.",
    )
    if r2.button("🗑 이번 회차 올린 파일 모두 지우기", key="v_purge"):
        try:
            r = http().delete(
                f"{VERSION_MANAGER}/api/v1/versions/{round_id}", timeout=60.0
            )
        except Exception as exc:
            st.error(str(exc))
        else:
            if r.status_code >= 300:
                st.error(error_text(r))
            else:
                data = unwrap(r) or {}
                done, failed = reset_round_downstream(round_id)
                st.success(
                    f"파일 {data.get('deleted_versions')}개를 지웠습니다 — "
                    "이번 회차는 비어 있습니다."
                )
                report_reset(done, failed)

    uploads = st.file_uploader(
        "지원자 엑셀 파일 (여러 개 한꺼번에 고를 수 있습니다)", type=["xlsx"],
        accept_multiple_files=True, key="v_uploads",
    )
    if uploads:
        kinds: list[str] = []
        st.markdown("**파일 종류를 이렇게 읽었습니다** — 다르면 바로 고쳐 주세요")
        for index, upload in enumerate(uploads):
            auto_kind, auto_team = classify_local(upload.name)
            c1, c2, c3 = st.columns([4, 2, 2])
            c1.write(f"📄 **{upload.name}** · {len(upload.getvalue()) / 1024:.0f} KB")
            kind = c2.selectbox(
                "종류", [KIND_MASTER, KIND_TEAM],
                index=0 if auto_kind == KIND_MASTER else 1,
                format_func=lambda k: KIND_LABELS.get(k, k),
                key=f"v_kind_{index}", label_visibility="collapsed",
            )
            team = c3.text_input(
                "팀", value=auto_team, key=f"v_team_{index}",
                placeholder="팀별 명단이면 팀 이름", label_visibility="collapsed",
            ).strip()
            if kind == KIND_TEAM and not team:
                team = "미상"
            kinds.append(f"{kind}:{team}" if kind == KIND_TEAM else kind)

        if st.button("📥 올리기", type="primary", key="v_register"):
            files = [
                ("files", (u.name, u.getvalue(), XLSX_MIME)) for u in uploads
            ]
            try:
                r = http().post(
                    f"{VERSION_MANAGER}/api/v1/versions/register-batch",
                    files=files,
                    data={
                        "round_id": round_id, "actor": actor,
                        "kinds": ",".join(kinds),
                        "reset": "true" if replace else "false",
                    },
                    timeout=120.0,
                )
            except Exception as exc:
                st.error(str(exc))
            else:
                if r.status_code >= 300:
                    st.error(error_text(r))
                else:
                    data = unwrap(r) or {}
                    # 새로 올렸으니 앞선 대조·배포·시간표 결과는 모두 무효다.
                    # 화면 상태만 지우면 2단계가 서버에 남은 옛 배포안을 다시
                    # 집어 오므로, 서버까지 함께 비운다.
                    done, failed = reset_round_downstream(round_id)
                    st.session_state["v_registered"] = [
                        v["version_id"] for v in data.get("registered", [])
                    ]
                    cleared = data.get("cleared") or {}
                    st.success(
                        f"파일 {data.get('count')}개를 올렸습니다"
                        + (f" · 먼저 올렸던 {cleared.get('deleted_versions')}개는 "
                           "지웠습니다" if cleared.get("deleted_versions") else "")
                    )
                    report_reset(done, failed)
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "종류": KIND_LABELS.get(v.get("kind"), v.get("kind")),
                                "팀": v.get("team_name") or "-",
                                "파일 이름": v.get("file_name"),
                                "인원": v.get("applicant_count"),
                            }
                            for v in data.get("registered", [])
                        ]),
                        width="stretch", hide_index=True,
                    )
    elif DEFAULT_MASTER.exists():
        st.caption(f"올릴 파일이 없으면 예시 파일 {DEFAULT_MASTER.name} 을 쓸 수 있습니다.")

    # ---------------- 등록 이력 · 대조 대상 선택 ----------------
    st.divider()
    st.subheader("② 맞춰 볼 파일 고르기")
    history, err = fetch_json(f"{VERSION_MANAGER}/api/v1/versions/{round_id}/history")
    if err:
        st.error(err)
        return
    history = history or []
    if not history:
        st.info("이번 회차에 올린 파일이 없습니다. 위에서 먼저 올려 주세요.")
        return

    st.dataframe(
        pd.DataFrame([
            {
                "종류": KIND_LABELS.get(v.get("kind"), v.get("kind")),
                "팀": v.get("team_name") or "-",
                "파일 이름": v.get("file_name"),
                "인원": v.get("applicant_count"),
                "올린 사람": v.get("actor"),
                "올린 시각": str(v.get("created_at"))[:16],
                "현재 사용": "✅" if v.get("is_active") else "",
            }
            for v in history
        ]),
        width="stretch", hide_index=True, height=min(38 * (len(history) + 1) + 3, 320),
    )

    label = {
        v["version_id"]: f"{KIND_LABELS.get(v.get('kind'), v.get('kind'))} · "
                         f"{v.get('file_name') or '이름 없는 파일'}"
                         f" · {v.get('applicant_count')}명"
        for v in history
    }
    # 방금 등록한 것이 있으면 그것만, 없으면 이 회차에 올라온 전부를 기본 대상으로 둔다
    # (마스터를 다시 올리면 이전 마스터가 비활성이 되므로 활성만 보면 대조할 짝이 없다)
    default_ids = st.session_state.get("v_registered") or [
        v["version_id"] for v in history
    ]
    default_ids = [vid for vid in default_ids if vid in label]
    picked_ids = st.multiselect(
        "맞춰 볼 파일", list(label), default=default_ids,
        format_func=lambda vid: label[vid], key="v_compare_pick",
    )

    st.caption(
        "맞춰 보면 이번 회차의 확정 명단을 처음부터 다시 만드는 것이므로, "
        "먼저 만들어 둔 배포안 · 부서 회신 · 시간표 · 부서에 보낸 명단은 함께 지웁니다."
    )
    if st.button("🔍 맞춰 보기", type="primary", key="v_compare") and picked_ids:
        data, cerr = post_json(
            f"{VERSION_MANAGER}/api/v1/versions/compare", {"version_ids": picked_ids}
        )
        if cerr:
            st.error(cerr)
        else:
            # 여기서부터 명단을 다시 정하므로 뒤 단계는 전부 무효다. 화면 상태만
            # 지우면 2단계가 서버에 남은 옛 배포안을 되찾아 오므로 서버까지 비운다.
            # 지금 화면에 이미 그려진 '맞춰 볼 파일' 은 그대로 둔다.
            done, failed = reset_round_downstream(round_id, keep=("v_compare_pick",))
            st.session_state["v_compare_result"] = data
            st.session_state["v_selections"] = {}
            report_reset(done, failed)

    result = st.session_state.get("v_compare_result")
    if not result:
        return

    # ---------------- 대조 결과 ----------------
    st.divider()
    st.subheader("③ 맞춰 본 결과")
    names = {v["version_id"]: v.get("file_name") or "이름 없는 파일"
             for v in result.get("versions", [])}

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("전체 명단 파일", len(result.get("master_version_ids", [])))
    m2.metric("팀별 명단 파일", len(result.get("team_version_ids", [])))
    m3.metric("값이 다른 지원자", result.get("conflict_count", 0))
    m4.metric("값이 같은 지원자", result.get("identical_count", 0))

    for bad in result.get("unreadable", []):
        st.error(f"열지 못한 파일이 있습니다 — {bad.get('reason')}")

    only_in = {vid: ids for vid, ids in (result.get("only_in") or {}).items() if ids}
    if only_in:
        with st.expander(f"한 파일에만 있는 지원자 ({sum(len(v) for v in only_in.values())}명)"):
            for vid, ids in only_in.items():
                st.markdown(f"**{names.get(vid, vid)}** 에만 있는 사람 {len(ids)}명")
                st.write(", ".join(ids[:200]) + (" …" if len(ids) > 200 else ""))

    integrity = result.get("integrity")
    if integrity:
        st.markdown("#### 🧾 빠진 사람 · 겹친 사람 확인")
        status = integrity.get("status")
        if status == "OK":
            st.success("전체 명단과 팀별 명단이 서로 맞습니다.")
        else:
            st.warning("전체 명단과 팀별 명단이 어긋납니다 — 아래 표를 확인해 주세요.")
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("전체 명단 인원", integrity.get("master_count"))
        i2.metric("팀에 나간 인원", integrity.get("distributed_count"))
        i3.metric("어느 팀에도 없음", integrity.get("undistributed_count"))
        i4.metric("두 팀에 겹침", integrity.get("duplicate_count"))
        issues = integrity.get("issues") or []
        if issues:
            rows = []
            for issue in issues:
                if issue["type"] == "UNDISTRIBUTED":
                    rows.append({
                        "무엇이": "어느 팀에도 없음",
                        "누가": f"{issue.get('count')}명",
                        "자세히": ", ".join((issue.get("applicant_ids") or [])[:50]),
                    })
                else:
                    rows.append({
                        "무엇이": "두 팀에 겹침",
                        "누가": issue.get("applicant_id"),
                        "자세히": ", ".join(issue.get("teams") or []),
                    })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    conflicts = result.get("conflicts") or []
    masters = [vid for vid in result.get("master_version_ids", []) if vid in names]
    # 마스터로 판별된 파일이 없어도(배포본 이름으로만 올린 회차) 등록한 파일 자체가
    # 곧 최종 취합본이다 — 읽힌 파일 전부를 병합 대상으로 쓴다.
    teams_only = not masters
    if teams_only:
        broken = {b.get("version_id") for b in (result.get("unreadable") or [])}
        readable = [v["version_id"] for v in result.get("versions", [])
                    if v["version_id"] not in broken]
        masters = [vid for vid in (result.get("mergeable_version_ids") or readable)
                   if vid in names]
    selections: dict[str, str] = st.session_state.setdefault("v_selections", {})

    st.markdown("#### ⚖️ 파일마다 값이 다른 지원자 — 어느 파일 값을 쓸까요")
    if not conflicts:
        st.success("값이 다른 지원자가 없습니다 — 고를 것이 없어 그대로 확정 명단이 됩니다.")
        selections.clear()
    else:
        b1, b2 = st.columns([3, 1])
        bulk = b1.selectbox(
            "전부 이 파일 값으로", masters, format_func=lambda v: names.get(v, v),
            key="v_bulk",
        ) if masters else None
        b2.write("")
        if bulk and b2.button("전체 적용", key="v_bulk_apply"):
            for conflict in conflicts:
                if bulk in conflict["present_in"]:
                    selections[conflict["applicant_id"]] = bulk
            st.rerun()

        page_size = 20
        pages = (len(conflicts) - 1) // page_size + 1
        page = st.number_input(
            f"페이지 (총 {len(conflicts)}건 · {pages}쪽)", 1, pages, 1, key="v_page"
        ) if pages > 1 else 1
        window = conflicts[(page - 1) * page_size: page * page_size]

        for conflict in window:
            aid = conflict["applicant_id"]
            with st.container(border=True):
                st.markdown(
                    f"**{conflict.get('name') or '(이름 없음)'}** · 지원자 번호 {aid}"
                )
                diff_rows = []
                for field in conflict["fields"]:
                    row = {"항목": field["column"]}
                    for vid, value in field["values"].items():
                        row[names.get(vid, vid)] = value
                    diff_rows.append(row)
                st.dataframe(pd.DataFrame(diff_rows), width="stretch", hide_index=True)

                present = conflict["present_in"]
                current = selections.get(aid, present[0])
                index = present.index(current) if current in present else 0
                choice = st.radio(
                    "이 사람은 어느 파일 값으로 할까요", present, index=index,
                    horizontal=True, format_func=lambda v: names.get(v, v),
                    key=f"v_sel_{aid}",
                )
                selections[aid] = choice

        undecided = [c["applicant_id"] for c in conflicts if c["applicant_id"] not in selections]
        st.caption(
            f"{len(conflicts) - len(undecided)} / {len(conflicts)}명 골랐습니다 "
            "— 고르지 않은 사람은 기준 파일 값을 그대로 씁니다."
        )

    # ---------------- 최종 파일 생성 ----------------
    st.divider()
    st.subheader("④ 확정 명단 만들기")
    if not masters:
        rest = [v for v in history if v["version_id"] not in picked_ids]
        if rest:
            st.warning(
                "맞춰 보기에서 빠진 파일이 있어 확정 명단을 만들 수 없습니다. 위 ②에서 "
                "아래 파일까지 골라 다시 맞춰 보세요 — "
                + ", ".join(v.get("file_name") or "이름 없는 파일" for v in rest)
            )
            if st.button("🔁 전부 넣어 다시 맞춰 보기", key="v_add_master"):
                st.session_state["v_registered"] = [
                    v["version_id"] for v in history
                ]
                st.session_state.pop("v_compare_pick", None)
                st.session_state.pop("v_compare_result", None)
                st.rerun()
        else:
            st.warning(
                "올린 파일을 하나도 열지 못했습니다. ①에서 엑셀(.xlsx) 파일을 다시 "
                "올려 주세요 — 올린 파일이 곧 확정 명단이 되고, 2단계가 그 명단으로 "
                "팀을 나눕니다."
            )
        return

    if teams_only:
        st.info(
            "이번 회차에는 팀별 명단만 올라와 있어, 올린 파일들을 그대로 합쳐 확정 "
            "명단을 만듭니다. (전체 명단 파일을 따로 올리지 않아도 됩니다)"
        )

    c1, c2 = st.columns([2, 3])
    base = c1.selectbox("기준으로 삼을 파일", masters,
                        format_func=lambda v: names.get(v, v), key="v_base")
    out_name = c2.text_input("만들 파일 이름", value=f"취합_최종_{round_id}.xlsx",
                             key="v_outname")

    def make_final(base_id: str, file_name: str) -> str | None:
        data, merr = post_json(
            f"{VERSION_MANAGER}/api/v1/versions/merge",
            {
                "round_id": round_id,
                "base_version_id": base_id,
                "version_ids": masters,
                "selections": selections,
                "actor": actor,
                "file_name": file_name,
            },
        )
        if merr:
            return merr
        st.session_state["merged_version"] = data
        st.session_state["master_version_id"] = data.get("version_id")
        clear_caches()
        return None

    # 어긋난 값이 없으면 고를 것도 없다 — 대조한 그대로 최종 취합본으로 확정한다
    auto_key = "|".join(sorted(masters))
    if not conflicts and masters and st.session_state.get("v_auto_key") != auto_key:
        st.session_state["v_auto_key"] = auto_key
        err_auto = make_final(base, out_name)
        if err_auto:
            st.error(err_auto)
        else:
            st.session_state["v_auto_done"] = True
    if conflicts:
        st.session_state.pop("v_auto_key", None)
        st.session_state.pop("v_auto_done", None)
    if st.session_state.get("v_auto_done"):
        st.info("값이 다른 지원자가 없어 맞춰 본 결과를 그대로 확정 명단으로 만들었습니다.")

    if st.button("🧬 확정 명단 만들기", type="primary", key="v_merge"):
        err_merge = make_final(base, out_name)
        if err_merge:
            st.error(err_merge)
        else:
            st.session_state["v_auto_done"] = False

    merged = st.session_state.get("merged_version")
    if merged:
        st.success(f"확정 명단을 만들었습니다 — {merged.get('file_name')}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("지원자", merged.get("applicant_count"))
        m2.metric("줄 수", merged.get("row_count"))
        m3.metric("못 정한 사람", len(merged.get("unresolved") or []))
        m4.metric("담당팀 붙음", merged.get("teamed_count"))
        st.caption(f"만든 시각 {str(merged.get('created_at'))[:16]}")

        # 팀 나눔이 이 파일에 실제로 새겨졌는지 여기서 눈으로 확인할 수 있어야 한다.
        # 엑셀을 열어 보기 전에는 컬럼이 생겼는지 알 길이 없어서, 예전 취합본을
        # 보고 "안 붙었다" 고 오해하기 쉬웠다. 이 컬럼이 2단계 [팀 배정하기] 의
        # 유일한 근거이므로, 비어 있으면 그 자리에서 알려 준다.
        column = merged.get("team_column")
        teamed = merged.get("teamed_count")
        if column and teamed is not None:
            duplicated = merged.get("team_duplicate_count") or 0
            teamless = merged.get("teamless") or []
            st.caption(
                f"`{column}` 칸에 팀 이름을 적어 두었습니다 — {teamed}명. "
                + (f"두 팀 이상이 적어 낸 {duplicated}명은 한 칸에 쉼표로 이어 "
                   "두었고, 2단계 [팀 배정하기] 에서 그대로 중복면접이 됩니다. "
                   if duplicated else "")
                + "2단계는 이 칸을 읽어 팀을 나눕니다."
            )
            if teamless:
                st.info(
                    f"어느 팀도 적어 내지 않은 지원자 {len(teamless)}명은 `{column}` 이 "
                    "비어 있습니다 — 2단계 [팀 배정하기] 를 누르면 이 사람들만 규칙대로 "
                    "(지망 조직·직무·전공·정원) 나눠 담습니다. 팀이 적어 낸 사람은 그대로 "
                    "둡니다. 특정 팀에 넣으려면 팀별 명단(`희망지원자_팀이름`)을 올린 뒤 "
                    "확정 명단을 다시 만들어 주세요."
                )

        vid = merged["version_id"]
        blob, berr = fetch_bytes(f"{VERSION_MANAGER}/api/v1/versions/by-id/{vid}/file")
        if berr:
            st.error(berr)
        else:
            st.download_button(
                "⬇ 확정 명단 내려받기", blob, file_name=merged.get("file_name"),
                mime=XLSX_MIME, key="v_download",
            )

        preview, perr = fetch_json(
            f"{VERSION_MANAGER}/api/v1/versions/by-id/{vid}/preview", (("limit", 100),)
        )
        if perr:
            st.error(perr)
        elif preview:
            st.markdown(f"**미리 보기** — 전체 {preview.get('total_rows')}명 중 앞부분")
            frame = pd.DataFrame(preview.get("rows") or [])
            # '담당팀' 은 원본 컬럼을 건드리지 않으려고 파일 맨 뒤에 붙는다. 컬럼이
            # 50개가 넘어서 그대로 두면 가로로 한참 밀어야 보이므로, 미리 보기에서만
            # 앞으로 당긴다 — 방금 새긴 팀 나눔을 바로 눈으로 확인할 수 있어야 한다.
            if column and column in frame.columns:
                frame = frame[[column] + [c for c in frame.columns if c != column]]
            st.dataframe(frame, width="stretch", hide_index=True, height=420)


# ============================================================
# 2. 지원자 명단 정리
# ============================================================
NAME_HEADER = "한글성명"
ID_HEADER = "지원자 번호"
DEGREE_HEADER = "최종학력_학교유형"
BACHELOR_CODE = "과정1"

SLOT_MINUTES = 30      # 면접 1건
BREAK_MINUTES = 5      # 면접 사이 휴식
SLOTS_PER_DAY = len(SCHED_HOURS)   # 하루 최대 면접 건수 — 스케줄러 칸 수와 같다


def degree_label(value) -> str:
    """마스터의 학교유형 코드(과정1/2/3)를 학사·대학원으로 읽는다.

    배정 규칙(학사·대학원 균형)이 이 두 갈래를 쓰기 때문에 그대로 둔다. 화면에
    박사·석사를 갈라 보여 줄 때는 degree_full() 을 쓴다.
    """
    text = str(value or "").strip()
    if not text:
        return "미상"
    if text in (BACHELOR_CODE, "학사"):
        return "학사"
    return "대학원"


def degree_full(value) -> str:
    """카드 띠줄용 — 과정1/2/3 을 학사·석사·박사로 갈라 읽는다."""
    text = str(value or "").strip()
    if text in (BACHELOR_CODE, "학사"):
        return "학사"
    if text in ("과정2", "석사"):
        return "석사"
    if text in ("과정3", "박사"):
        return "박사"
    if text == "대학원":
        return "대학원"
    return "미상"


def team_rosters_from_plan(applicants: list[dict]) -> dict[str, list[dict]]:
    rosters: dict[str, list[dict]] = {}
    for row in applicants:
        rosters.setdefault(row["team"], []).append({
            "applicant_id": row.get("applicant_id", ""),
            "name": row.get("name", ""),
            "team": row["team"],
            "degree": degree_label(row.get("degree_type")),
            "degree_full": degree_full(row.get("degree_type")),
        })
    return rosters


def split_map(rosters: dict[str, list[dict]]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """팀별 명단을 '사람 → 속한 팀들' 과 '사람 → 이름' 으로 편다."""
    where: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for team in sorted(rosters or {}):
        for row in rosters[team] or []:
            key = str(row.get("applicant_id") or row.get("name") or "").strip()
            if not key:
                continue
            where.setdefault(key, []).append(team)
            names.setdefault(key, row.get("name") or key)
    return where, names


def team_moves(want: dict[str, list[str]], now: dict[str, list[str]],
               names: dict[str, str]) -> dict:
    """2단계에서 잡아 둔 차례와 지금 배정 결과가 어긋났으면 옮길 목록을 낸다.

    부서에 나가는 명단도, 시간표도 배정 결과(플랜)의 팀을 따른다. 2단계에서 순서를
    잡은 뒤에 배정을 다시 만들거나 사람을 옮기면, 인사 담당자가 확인하고 저장한
    차례와 실제로 나갈 명단이 갈라진다 — 같은 83명인데 54명이 다른 팀으로 간
    회차가 있었다.

    화면에서만 맞춰 보여 주면 더 나빠진다. 부서가 남의 팀 사람과 짝을 지으면
    스케줄러는 그 짝을 '지원자의 플랜 팀' 자리에 앉히므로(board.place 는
    applicant.team 으로 자리를 잡는다) 같은 팀 동시간 중복 금지가 엉뚱한 팀에
    걸린다. 팀 나눔은 한 군데 — 배정 결과 — 에서만 정해져야 한다.
    """
    moves, rows = [], []
    for key in sorted(want):
        mine, here = want[key], now.get(key)
        if not here or set(here) & set(mine):
            continue        # 아직 배정 결과에 없거나, 이미 그 팀들 중 하나에 있다
        moves.append({"applicant_id": key, "from": here[0], "to": mine[0],
                      "reason": "인사 담당자가 2단계에서 정한 팀"})
        rows.append({"성명": names.get(key, key), "지금 배정 결과": here[0],
                     "2단계에서 나눈 팀": mine[0]})
    return {
        "moves": moves,
        "rows": rows,
        # 2단계 나눔에서 두 팀이 함께 보겠다고 한 사람 — 배정 결과는 한 팀만
        # 담으므로 조정으로는 두 팀에 넣을 수 없다. 숨기지 않고 이름을 알린다.
        "dup": sorted(names.get(k, k) for k, v in want.items() if len(v) > 1),
        "unknown": sorted(names.get(k, k) for k in want if k not in now),
    }


def render_team_gap(plan_id: str, gap: dict, key: str) -> bool:
    """팀 나눔이 어긋났으면 그 사실과 맞추는 버튼을 보인다 (어긋났으면 True)."""
    if not gap["moves"]:
        return False
    st.error(
        f"**2단계에서 잡아 둔 차례와 지금 배정 결과가 {len(gap['moves'])}명 다릅니다.** "
        "부서에 나가는 명단도 4단계 시간표도 배정 결과의 팀을 따르므로, 이대로 "
        "두면 2단계에서 보신 것과 다른 명단이 부서에 갑니다."
    )
    with st.expander(f"팀이 다른 {len(gap['moves'])}명 보기"):
        st.dataframe(pd.DataFrame(gap["rows"]), width="stretch", hide_index=True,
                     height=min(420, 40 + 35 * len(gap["rows"])))
        if gap["dup"]:
            st.caption(
                f"두 팀이 함께 보는 {len(gap['dup'])}명은 옮기기로는 한 팀만 바뀝니다 — "
                + " · ".join(gap["dup"])
                + ". 2단계 ② 에서 차례를 다시 잡는 편이 깔끔합니다."
            )
        if gap["unknown"]:
            st.caption(f"배정 결과에 없는 {len(gap['unknown'])}명은 옮길 수 없습니다.")
        st.caption(
            "맞추면 팀 정원 · 학사 대학원 비율은 ① 이 계산한 값과 달라질 수 있습니다 — "
            "옮긴 사람에게는 HR_MANUAL 표가 붙습니다."
        )
    if st.button("🔁 배정 결과를 2단계 차례에 맞추기", type="primary", key=key,
                 disabled=not plan_id):
        data, err = post_json(
            f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/adjust",
            {"moves": gap["moves"], "actor": actor}, timeout=120.0,
        )
        if err:
            st.error(err)
        else:
            clear_caches()
            st.success(f"{len(gap['moves'])}명을 2단계에서 정한 팀으로 옮겼습니다.")
            st.rerun()
    return True


# 스케줄러가 쓰는 칸 순서. 옛 시간표에 남아 있는 시각 이름도 뒤에 붙여 둔다.
HOUR_ORDER = list(SCHED_HOURS) + ["09시", "10시", "11시", "12시", "13시",
                                  "14시", "15시", "16시", "17시", "18시"]


def hour_rank(hour) -> tuple:
    """시간대를 이른 것부터 — 모르는 표기는 뒤로 보낸다."""
    text = str(hour or "")
    return (HOUR_ORDER.index(text), text) if text in HOUR_ORDER else (99, text)


def hour_slots(assignments: list[dict]) -> list[str]:
    """시간표에 실제로 쓰인 시간대만 이른 순서로 — 시간표의 세로 축이 된다."""
    used = {str(row.get("hour")) for row in assignments if row.get("hour")}
    return sorted(used, key=hour_rank)


def slot_labels(start: str, count: int, minutes: int, rest: int) -> list[str]:
    """30분 면접 + 5분 휴식으로 이어지는 시간표 라벨."""
    cursor = datetime.strptime(start, "%H:%M")
    out = []
    for _ in range(count):
        end = cursor + timedelta(minutes=minutes)
        out.append(f"{cursor:%H:%M}~{end:%H:%M}")
        cursor = end + timedelta(minutes=rest)
    return out


def hour_clock(timing: dict) -> dict[str, str]:
    """스케줄러의 칸 이름(1타임 · 2타임 …)을 실제 시각으로 바꾼다.

    스케줄러는 하루를 여덟 칸으로 나눠 배치한다 — 그 칸이 몇 시 몇 분인지는
    3단계에서 정한 면접 진행 조건이 정한다. 여기서 이어 주지 않으면 3단계
    순서표는 09:35 라는데 4단계 시간표는 2타임이라고 해 서로 어긋난다.

    점심 시간은 따로 두지 않으므로 여덟 칸은 쉬는 시간만 끼고 죽 이어진다 —
    3단계 순서표를 그리는 slot_labels() 와 정확히 같은 계산이다.
    """
    try:
        return dict(zip(SCHED_HOURS, slot_labels(
            timing["start"], len(SCHED_HOURS), timing["minutes"], timing["rest"])))
    except ValueError:
        return {}


def band_supply(rows: list[dict], timing: dict | None = None) -> dict[str, int]:
    """칸마다 그 칸에 들어갈 수 있는 담당자가 몇 명인지.

    '뒤타임' 이라고 답한 사람은 이른 칸에 못 들어간다. 그래서 담당자 수가
    넉넉해도 특정 칸만 아무도 못 맡는 일이 생긴다 — 그 칸을 찾아낸다.
    """
    supply = {hour: 0 for hour in SCHED_HOURS}
    for row in rows or []:
        # 아직 가능 시간을 안 적어 낸 분은 어느 칸도 못 맡는 것으로 센다.
        # 스케줄러가 그렇게 읽기 때문이다 — 여기서만 '아무 때나 되는 사람' 으로
        # 세면 화면은 자리가 넉넉하다는데 최종 시간표에는 그 자리가 빈다.
        if not iv_answered(row):
            continue
        for hour in band_hours(band_of(row, timing), timing):
            supply[hour] += 1
    return supply


def team_seat_gap(rows: list[dict], timing: dict | None = None) -> list[str]:
    """팀마다 아무도 못 맡는 칸이 있는지 — 그 사유를 팀별 한 줄로 돌려준다.

    자리는 팀별로 잡히므로(같은 팀은 같은 시각에 두 명을 못 본다) 담당자가
    전체로는 넉넉해도 어느 팀에 뒤타임만 되는 사람뿐이면 그 팀의 이른 자리는
    끝내 빈다. 그래서 팀을 갈라 본다.
    """
    by_team: dict[str, list[dict]] = {}
    for row in rows or []:
        by_team.setdefault(str(row.get("team") or "미상"), []).append(row)

    clock = hour_clock({**DEFAULT_TIMING, **(timing or {})})
    out: list[str] = []
    for team in sorted(by_team):
        mine = by_team[team]
        supply = band_supply(mine, timing)
        empty = [hour for hour, count in supply.items() if count == 0]
        if not empty:
            continue
        times = " · ".join(clock.get(h, h) for h in empty)
        # 앞뒤가 겹치는 칸은 어느 쪽이든 맡을 수 있고, 어느 쪽도 아닌 칸은
        # '모든타임' 이라고 답한 사람만 맡을 수 있다.
        need: set[str] = set()
        for hour in empty:
            need |= set(hour_bands(hour, timing)) or {BAND_ALL}
        want = " 또는 ".join(sorted(need))
        out.append(
            f"**{team}** — {times} 칸은 담당자 {len(mine)}명 중 아무도 맡을 수 "
            f"없습니다. 적어 내신 가능 시간이 그 칸을 덮지 못해서입니다. "
            f"이 자리를 채우려면 '{want}' 가능한 담당자가 더 필요합니다."
        )
    return out


def empty_slot_reasons(assignments: list[dict], roster: list[dict]) -> list[dict]:
    """시간표 중간에 빈 칸이 왜 비었는지 팀 · 일차 · 칸별로 답한다.

    빈 칸 자체가 잘못은 아니다 — 담당자가 그 시간에 안 되거나, 소규모 그룹이
    첫 타임에 걸리지 않게 비워 두거나, 그날 그 팀 면접자가 거기까지 오지 않는
    경우가 있다. 다만 왜 비었는지 적어 두지 않으면 볼 때마다 실수처럼 보인다.

    맨 앞과 맨 뒤의 여백은 세지 않는다. 그건 그 팀 그날 면접이 그만큼이라는
    뜻이지 구멍이 아니다.
    """
    can: dict[str, dict[str, set[str]]] = {}      # 팀 → 일차 → 맡을 수 있는 칸
    cap: dict[str, int] = {}                      # 팀 → 그날 볼 수 있는 최대 인원
    for row in roster or []:
        team = row.get("team") or "미상"
        for day, hours in (row.get("availability") or {}).items():
            can.setdefault(team, {}).setdefault(day, set()).update(hours or [])
        cap[team] = cap.get(team, 0) + int(row.get("max_daily") or 0)

    used: dict[tuple[str, str], set[int]] = {}
    for row in assignments:
        hour = str(row.get("hour"))
        if hour in SCHED_HOURS:
            key = (row.get("team") or "미상", row.get("day") or "미정")
            used.setdefault(key, set()).add(SCHED_HOURS.index(hour))

    out: list[dict] = []
    for (team, day), slots in sorted(used.items()):
        servable = can.get(team, {}).get(day)
        for index in range(min(slots), max(slots) + 1):
            if index in slots:
                continue
            hour = SCHED_HOURS[index]
            if servable is not None and hour not in servable:
                why = "그 칸을 맡을 수 있는 담당자가 이 팀에 없습니다"
            elif len(slots) >= cap.get(team, 0) > 0:
                why = "이 팀 담당자들의 하루 한도가 다 찼습니다"
            else:
                why = "규칙(첫 타임 소규모 · 같은 팀 연속)을 지키느라 비웠습니다"
            out.append({"팀": team, "일차": day, "칸": hour, "왜 비었나": why})
    return out


def render_seat_moves(data: dict, doc: dict) -> None:
    """부서가 잡아 준 자리를 몇 개나 지켰는지, 못 지킨 자리는 왜 옮겼는지.

    부서는 자기가 확인하고 보낸 시간표를 기준으로 결과를 본다. 그 자리가
    바뀌었으면 바뀐 사실만이 아니라 까닭까지 알려야 납득할 수 있다.
    """
    total = int(data.get("dept_seats") or 0)
    if not total:
        return
    kept = int(data.get("dept_seats_kept") or 0)
    moved = data.get("dept_seats_moved") or {}
    if not moved:
        st.success(f"부서가 정해 보낸 자리 {total}건을 모두 그대로 지켰습니다.")
        return
    names = {}
    for block in (doc.get("teams") or {}).values():
        for row in block.get("applicants") or []:
            names[row["applicant_id"]] = row.get("name") or row["applicant_id"]
    st.warning(
        f"부서가 정해 보낸 자리 {total}건 중 {kept}건은 그대로 두었고 "
        f"{len(moved)}건은 자리를 옮겼습니다 — 까닭은 아래와 같습니다."
    )
    st.dataframe(
        [
            {"면접자": names.get(aid, aid), "지원자 번호": aid, "옮긴 까닭": tag_label(tag)}
            for aid, tag in sorted(moved.items(), key=lambda kv: names.get(kv[0], kv[0]))
        ],
        width="stretch", hide_index=True,
    )


def dup_conflicts(assignments: list[dict]) -> list[dict]:
    """같은 사람이 같은 시각에 두 팀에 잡힌 자리 — 04 와 같은 잣대로 센다.

    두 팀이 같이 보기로 한 사람은 자리가 둘이다. 그 둘이 같은 시각이면 사람이
    쪼개져야 하므로 시간표로 성립하지 않는다. 부서끼리는 서로의 시간표를 볼 수
    없어 부서 화면에서는 막을 수 없고, 여기서 처음 드러난다.
    """
    seen: dict[tuple, list[dict]] = {}
    for row in assignments or []:
        key = (row.get("applicant_id"), row.get("day"), row.get("hour"))
        seen.setdefault(key, []).append(row)
    out = []
    for (applicant_id, day, hour), mine in sorted(seen.items(), key=lambda kv: str(kv[0])):
        teams = sorted({str(row.get("team") or "") for row in mine})
        if len(mine) < 2 or len(teams) < 2:
            continue
        out.append({
            "applicant_id": applicant_id,
            "applicant_name": mine[0].get("applicant_name") or applicant_id,
            "day": day, "hour": hour, "teams": teams,
        })
    return out


def render_dup_fix(sc_id: str, assignments: list[dict],
                   days: dict | None = None, timing: dict | None = None) -> None:
    """중복면접자 시간 겹침을 알리고, 그 사람만 다시 앉힐 수 있게 한다.

    인사가 1차로 보낸 명단에서 부서가 어떤 사람을 안 고르면 그 사람은 빠지고
    빈 자리는 다른 사람으로 메워진다. 그렇게 메운 자리가 다른 팀이 같은 사람을
    같은 시각에 잡아 둔 자리와 겹치는 것이다.

    고칠 때도 겹친 사람만 옮긴다 — 시간표를 통째로 다시 만들면 부서가 확인하고
    보낸 자리가 매번 다른 물건이 된다.
    """
    clock = hour_clock({**DEFAULT_TIMING, **(timing or {})})
    # 고친 결과는 다음 화면에서 보여 준다 — 고치자마자 새로 그리므로, 누른 그
    # 자리에서 적어 두면 화면이 바뀌면서 같이 사라진다.
    render_dup_fixed(st.session_state.pop("s_fixdup_done", None), clock)

    rows = dup_conflicts(assignments)
    if not rows:
        return
    st.error(
        f"**두 팀 면접이 같은 시각에 잡힌 면접자 {len(rows)}명**입니다 — 한 사람이 "
        "같은 시각에 두 면접장에 앉을 수는 없습니다. 부서에서 안 고른 사람의 자리를 "
        "다른 사람으로 메우면서 생깁니다. 아래 '오류 수정하기'를 누르면 **이 분들의 "
        "자리만** 담당자 가능 시간에 맞춰 옮기고, 나머지 자리는 그대로 둡니다."
    )
    st.dataframe(
        [
            {
                "면접자": row["applicant_name"],
                "지원자 번호": row["applicant_id"],
                "겹친 시각": f"{row['day']} {clock.get(row['hour'], row['hour'])}",
                "겹친 팀": " · ".join(row["teams"]),
            }
            for row in rows
        ],
        width="stretch", hide_index=True,
    )
    if st.button("🛠 오류 수정하기 (겹친 분만 다시 배치)", key="s_fixdup",
                 type="primary"):
        data, err = post_json(
            f"{SCHEDULER}/api/v1/schedules/{sc_id}/fix-duplicates",
            {"days_by_team": days or {}, "actor": actor},
            timeout=60.0,
        )
        if err:
            st.error(err)
            return
        clear_caches()
        st.session_state["s_fixdup_done"] = data
        st.rerun()


def render_dup_fixed(data: dict | None, clock: dict) -> None:
    """방금 고친 결과 — 누구를 어디서 어디로 옮겼는지, 못 옮긴 건 왜인지."""
    if not data:
        return
    moved = data.get("moved") or []
    stuck = data.get("stuck") or []
    if not moved and not stuck:
        st.info("고칠 곳이 없었습니다 — 겹친 자리가 남아 있지 않습니다.")
        return
    if moved:
        st.success(
            f"{len(moved)}건을 옮겨 겹침을 풀었습니다 — 다른 자리는 건드리지 "
            "않았습니다. 옮긴 자리에는 '두 팀 면접이 같은 시각이라 옮김' 이 "
            "사유로 남습니다."
        )
        st.dataframe(
            [
                {
                    "면접자": row.get("applicant_name") or row.get("applicant_id"),
                    "팀": row.get("team", ""),
                    "옮기기 전": f"{row.get('from_day', '')} "
                              f"{clock.get(row.get('from_hour'), row.get('from_hour', ''))}",
                    "옮긴 자리": f"{row.get('day', '')} "
                              f"{clock.get(row.get('hour'), row.get('hour', ''))}",
                }
                for row in moved
            ],
            width="stretch", hide_index=True,
        )
    if stuck:
        st.warning(
            f"{len(stuck)}건은 옮길 자리를 찾지 못했습니다 — "
            + ("확정해 둔 자리는 건드리지 않습니다. "
               if any(row.get("reason") == "LOCKED" for row in stuck) else "")
            + "그 팀 면접일 안에 담당자가 가능한 빈 칸이 없습니다. 3단계에서 그 "
              "담당자의 가능한 시간을 더 받거나, 부서에 시간을 다시 잡아 달라고 "
              "해 주세요."
        )
        st.dataframe(
            [
                {
                    "면접자": row.get("applicant_name") or row.get("applicant_id"),
                    "팀": row.get("team", ""),
                    "그대로 둔 자리": f"{row.get('day', '')} "
                                f"{clock.get(row.get('hour'), row.get('hour', ''))}",
                    "까닭": ("확정해 둔 자리" if row.get("reason") == "LOCKED"
                           else "옮길 빈 칸이 없음"),
                }
                for row in stuck
            ],
            width="stretch", hide_index=True,
        )


def render_off_band(rows: list[dict] | None, timing: dict | None = None) -> None:
    """담당자 사정과 어긋나게 잡힌 자리 — 누구와 다시 이야기해야 하는지.

    '담당자 일정 무시하고 배치하기'를 켜서 만들면 나온다. 규칙 위반은
    아니지만(인사가 일부러 고른 것이다) 숨기면 전화할 곳을 모르게 된다.
    """
    rows = rows or []
    if not rows:
        return
    clock = hour_clock({**DEFAULT_TIMING, **(timing or {})})
    names = iv_names((fetch_json(f"{SCHEDULER}/api/v1/interviewers")[0]) or [])
    st.warning(
        f"담당자 가능 시간과 어긋난 자리가 {len(rows)}건입니다 — 자리를 채우려고 "
        "일정을 무시하고 잡았습니다. 아래 분들과 따로 이야기해 주세요."
    )
    st.dataframe(
        [
            {
                "팀": row.get("team", ""),
                "담당자": names.get(row.get("interviewer_id"),
                                 row.get("interviewer_id", "")),
                "면접자": row.get("applicant_id", ""),
                "언제": f"{row.get('day', '')} "
                        f"{clock.get(row.get('hour'), row.get('hour', ''))}",
                "사유": str(row.get("message", "")).split("—")[-1].strip(),
            }
            for row in rows
        ],
        width="stretch", hide_index=True,
    )


def band_hours_text(band: str, timing: dict | None = None) -> str:
    """그 덩어리가 실제로 몇 시부터 몇 시까지인지 — 화면 설명용."""
    clock = hour_clock({**DEFAULT_TIMING, **(timing or {})})
    picked = [clock.get(h, h) for h in band_hours(band, timing)]
    if not picked:
        return "없음"
    return f"{picked[0].split('~')[0]}~{picked[-1].split('~')[1]}"


DEGREE_ORDER = {"박사": 0, "석사": 1, "대학원": 1, "학사": 2}


def degree_key(row: dict) -> str:
    """학력을 가장 자세한 표기로 — 박사·석사가 '대학원' 하나로 뭉치지 않게."""
    return str(row.get("degree_full") or row.get("degree") or "").strip()


def degree_rank(row: dict) -> tuple:
    """하루 안에서 어느 학력을 먼저 볼지 — 박사 · 석사 · 학사 순."""
    text = degree_key(row)
    return (DEGREE_ORDER.get(text, 9), text)


def order_for_interview(rows: list[dict], balance: bool,
                        per_day: int = 0) -> list[dict]:
    """가나다순을 기본으로 하되, 학력이 한쪽 날짜로 몰리지 않게 번갈아 배치한다.

    날짜끼리는 고르게 섞고, 같은 날 안에서는 같은 학력끼리 이어 붙인다 —
    면접관이 하루 종일 박사·석사·학사를 오가지 않고 묶어서 보게 된다.
    """
    if any(r.get("order") for r in rows):
        # 인사 담당자가 2단계에서 이미 순서를 잡아 보냈으면 그 순서가 기준이다 —
        # 부서 화면에서 다시 섞으면 초안과 시간표가 어긋난다.
        return sorted(rows, key=lambda r: (r.get("order") or 10 ** 6,
                                           r.get("name") or ""))

    ordered = sorted(rows, key=lambda r: (r.get("name") or ""))
    if not balance:
        return ordered

    buckets: dict[str, list[dict]] = {}
    for row in ordered:
        buckets.setdefault(degree_key(row), []).append(row)
    if len(buckets) < 2:
        return ordered

    taken = {key: 0 for key in buckets}
    out: list[dict] = []
    for _ in range(len(ordered)):
        # 남은 비율이 가장 큰 그룹에서 한 명씩 뽑으면 학력이 고르게 섞인다
        key = max(
            (k for k in buckets if taken[k] < len(buckets[k])),
            key=lambda k: (len(buckets[k]) - taken[k]) / len(buckets[k]),
        )
        out.append(buckets[key][taken[key]])
        taken[key] += 1

    if per_day > 0:
        # 하루치씩 끊어서 그 안에서만 담당자 · 학력끼리 붙인다 (날짜별 구성비는 그대로).
        # 담당자를 앞세우는 이유는 한 사람이 연달아 보고 끝내게 하기 위해서다 —
        # 담당자가 아직 없는 화면(2단계 명단 정리)에서는 빈 문자열이라 영향이 없다.
        blocked: list[dict] = []
        for start in range(0, len(out), per_day):
            blocked += sorted(
                out[start:start + per_day],
                key=lambda r: (str(r.get("interviewer_id") or ""), degree_rank(r),
                               r.get("name") or ""),
            )
        return blocked
    return out


def build_day_table(
    rosters: dict[str, list[dict]], *, start: str, minutes: int, rest: int,
    per_day: int, balance: bool, show_degree: bool,
) -> tuple[pd.DataFrame, int, dict[str, list[dict]]]:
    """팀을 가로, 시간을 세로로 놓고 일자마다 한 줄 띄운 면접 순서표를 만든다.

    표와 함께 '누가 몇 번째인지' 도 돌려준다 — 이 순번이 3단계에서 부서로 함께
    넘어가서, 부서 화면과 부서 시간표가 인사 담당자가 잡은 차례를 그대로 따른다.
    """
    teams = sorted(rosters)
    ordered = {team: order_for_interview(rosters[team], balance, per_day)
               for team in teams}
    longest = max((len(v) for v in ordered.values()), default=0)
    days = max(1, -(-longest // per_day))  # 올림
    labels = slot_labels(start, per_day, minutes, rest)

    blank = {team: "" for team in teams}
    rows: list[dict] = []
    for day in range(days):
        rows.append({"구분": f"── {day + 1}일차 ──", **blank})
        for index, label in enumerate(labels):
            position = day * per_day + index
            row = {"구분": label}
            for team in teams:
                person = ordered[team][position] if position < len(ordered[team]) else None
                if person is None:
                    row[team] = ""
                elif show_degree:
                    row[team] = f"{person['name']} ({person['degree']})"
                else:
                    row[team] = person["name"]
            rows.append(row)
        if day < days - 1:
            rows.append({"구분": "", **blank})  # 일자 사이 빈 줄

    sequence = {
        team: [
            {
                "applicant_id": person.get("applicant_id") or "",
                "name": person.get("name") or "",
                "order": index + 1,
                "day": index // per_day + 1,
                "time": labels[index % per_day],
            }
            for index, person in enumerate(people)
        ]
        for team, people in ordered.items()
    }
    return pd.DataFrame(rows, columns=["구분"] + teams), days, sequence


def to_excel(frames: dict[str, pd.DataFrame]) -> bytes:
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet[:31], index=False)
    return buffer.getvalue()


def render_roster_organizer(history: list[dict], plan_id: str,
                            master_id: str | None = None) -> None:
    """팀별 명단(가로=팀 · 세로=가나다순) + 일자별 면접 순서표."""
    st.subheader("② 팀별 면접 순서 잡기")
    st.caption(
        "① 에서 만든 배정 결과 그대로입니다 — 여기서 팀을 다시 고르지 않습니다. "
        "팀마다 지원자를 가나다순으로 세우고, [면접 순서 잡기]를 누르면 날짜마다 학력이 "
        "고르게 섞이도록 차례를 정해 하루 8명씩(30분 면접 + 5분 휴식) 나눕니다. "
        "하루 안에서는 박사 → 석사 → 학사 순으로 같은 학력끼리 붙여 놓습니다."
    )

    # 명단은 ① 이 만든 배정 결과 하나에서만 온다. 예전에는 여기서 '1단계 파일'
    # 과 '배정 결과' 중 하나를 고르게 했는데, 파일 쪽을 골라 순서를 잡아도 부서에
    # 나가는 명단과 4단계 시간표는 배정 결과를 따라서 둘이 갈라졌다. 1단계 나눔을
    # 그대로 쓰고 싶으면 ① 의 [팀 배정하기] 가 그 일을 한다 — 고르는 자리는 거기다.
    if not plan_id:
        st.info(
            "아직 팀이 나뉜 명단이 없습니다. 1단계에서 만든 확정 명단을 팀별로 나눈 뒤 "
            "바로 순서를 잡아 드립니다."
        )
        if master_id and st.button("🗂️ 확정 명단으로 팀 나누기", type="primary",
                                   key="r_from_master"):
            data, perr = post_json(
                f"{DISTRIBUTOR}/api/v1/distribute/plan",
                {
                    "round_id": round_id, "master_version_id": master_id,
                    "mode": "inherit", "allow_duplicate": True,
                    "duplicate_score_threshold": 0.8, "created_by": actor,
                },
                timeout=180.0,
            )
            if perr:
                st.error(perr)
            else:
                st.session_state["plan_id"] = (data or {}).get("plan_id")
                st.session_state["plan_summary"] = data
                clear_caches()
                st.rerun()
        return

    applicants, err = fetch_json(
        f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/applicants"
    )
    if err:
        st.error(err)
        return
    rosters = team_rosters_from_plan(applicants or [])

    rosters = {team: rows for team, rows in rosters.items() if rows}
    if not rosters:
        st.warning("불러온 명단이 비어 있습니다.")
        return

    teams = sorted(rosters)
    team_colors(teams)

    # 두 팀 이상이 같은 사람을 보겠다고 한 경우 — 카드에 표시해 준다
    wanted: dict[str, set] = {}
    for team, rows in rosters.items():
        for row in rows:
            key = row.get("applicant_id") or row.get("name")
            if key:
                wanted.setdefault(key, set()).add(team)
    duplicated = {key for key, owners in wanted.items() if len(owners) > 1}
    dup_names = {
        row.get("name")
        for rows in rosters.values() for row in rows
        if (row.get("applicant_id") or row.get("name")) in duplicated
    }

    longest = max(len(rows) for rows in rosters.values())
    matrix = pd.DataFrame(
        {
            team: [
                r["name"] for r in sorted(rosters[team], key=lambda x: x["name"] or "")
            ] + [""] * (longest - len(rosters[team]))
            for team in teams
        },
        index=range(1, longest + 1),
    )

    # 중복면접자는 팀마다 카드가 흩어져 있어 눈으로 잇기 어렵다 — 한 사람을 고르면
    # 그 사람의 카드가 모든 팀에서 함께 빨갛게 표시되게 한다.
    dup_label = {
        key: next((r.get("name") for rows in rosters.values() for r in rows
                   if (r.get("applicant_id") or r.get("name")) == key), key)
        for key in sorted(duplicated)
    }
    focus = ""
    if duplicated:
        st.markdown("**중복면접자 짚어 보기**")
        st.caption(
            "이름을 누르면 그 사람이 들어간 팀의 카드가 모두 빨간 테두리로 함께 "
            "표시됩니다. 한 번 더 누르면 표시가 풀립니다."
        )
        options = list(dup_label)
        if hasattr(st, "pills"):
            focus = st.pills(
                "중복면접자", options, key="r_dup_focus", label_visibility="collapsed",
                format_func=lambda k: f"{dup_label[k]} · {len(wanted[k])}팀",
            ) or ""
        else:   # 옛 버전 대비 — 고르는 방식만 다르고 표시는 같다
            choice = st.selectbox(
                "중복면접자", ["(고르지 않음)"] + options, key="r_dup_focus",
                label_visibility="collapsed",
                format_func=lambda k: (k if k.startswith("(")
                                       else f"{dup_label[k]} · {len(wanted[k])}팀"),
            )
            focus = "" if choice.startswith("(") else choice
        if focus:
            st.info(
                f"**{dup_label[focus]}** 님은 {' · '.join(sorted(wanted[focus]))} "
                f"{len(wanted[focus])}개 팀이 함께 보겠다고 했습니다 — 아래에서 빨간 "
                "테두리가 붙은 카드가 같은 사람입니다."
            )

    for team in teams:
        people = sorted(rosters[team], key=lambda r: r.get("name") or "")
        day_title(f"🏢 {team} — {len(people)}명")
        card_grid([
            card(f"{index}번", row.get("name") or row.get("applicant_id"), team,
                 tone="pick" if (row.get("applicant_id")
                                 or row.get("name")) == focus else "",
                 team=team, degree=row.get("degree_full") or row.get("degree"),
                 badge="중복면접" if (row.get("applicant_id")
                                  or row.get("name")) in duplicated else "")
            for index, row in enumerate(people, start=1)
        ], cols=6)
    st.caption(
        " · ".join(f"{team} {len(rosters[team])}명" for team in teams)
        + f" · 모두 {sum(len(v) for v in rosters.values())}명 (팀별 가나다순) · "
        "카드 색은 부서, 위쪽 띠줄은 학력입니다."
        + (f" · 두 팀 이상이 함께 원하는 지원자 {len(duplicated)}명은 "
           "[중복면접] 표가 붙습니다." if duplicated else "")
    )
    if duplicated:
        with st.expander(f"중복면접 {len(duplicated)}명 — 어느 팀끼리 겹쳤나"):
            st.dataframe(
                pd.DataFrame([
                    {
                        "지원자": next(
                            (r.get("name") for rows in rosters.values() for r in rows
                             if (r.get("applicant_id") or r.get("name")) == key),
                            key),
                        "겹친 팀": " · ".join(sorted(wanted[key])),
                        "면접 횟수": len(wanted[key]),
                    }
                    for key in sorted(duplicated)
                ]),
                width="stretch", hide_index=True,
            )
    with st.expander("표로 보기"):
        st.dataframe(matrix, width="stretch", height=min(38 * (longest + 1) + 3, 520))

    st.markdown("**면접 진행 조건**")
    c1, c2, c3, c4 = st.columns(4)
    start = c1.text_input("몇 시부터", value="09:00", key="r_start")
    minutes = c2.number_input("한 명당 면접(분)", 10, 120, SLOT_MINUTES, 5, key="r_min")
    rest = c3.number_input("사이 쉬는 시간(분)", 0, 60, BREAK_MINUTES, 5, key="r_rest")
    per_day = c4.number_input("하루 몇 명까지", 1, 20, SLOTS_PER_DAY, key="r_perday")
    o1, o2 = st.columns(2)
    balance = o1.checkbox("날짜마다 학력 고르게 · 하루 안에서는 학력끼리 묶기",
                          value=True, key="r_balance")
    show_degree = o2.checkbox("학력도 같이 보기", value=True, key="r_showdeg")

    if st.button("🗂️ 면접 순서 잡기", type="primary", key="r_organize"):
        try:
            table, days, sequence = build_day_table(
                rosters, start=start.strip(), minutes=int(minutes), rest=int(rest),
                per_day=int(per_day), balance=balance, show_degree=show_degree,
            )
        except ValueError:
            st.error("시작 시각은 HH:MM 형식으로 입력하세요. (예: 09:00)")
        else:
            st.session_state["roster_table"] = table
            st.session_state["roster_days"] = days
            st.session_state["roster_matrix"] = matrix
            # 여기서 잡은 차례와 그 차례를 만든 조건을 적어 둬야 3단계에서 명단과
            # 함께 부서로 넘어가고, 4단계 시간표도 같은 시각으로 그린다
            save_order(round_id, sequence, {
                "start": start.strip(), "minutes": int(minutes),
                "rest": int(rest), "per_day": int(per_day),
            })

    table = st.session_state.get("roster_table")
    if table is None:
        return

    days = st.session_state.get("roster_days", 0)
    st.success(
        f"{days}일에 걸쳐 · 하루 {int(per_day)}명씩 · 한 명당 {int(minutes)}분 면접 + "
        f"{int(rest)}분 휴식으로 순서를 잡았습니다."
        + (" 하루 안에서는 박사 → 석사 → 학사 순으로 묶여 있습니다." if balance else "")
        + " 이 차례(순번)와 위 조건은 3단계에서 명단과 함께 각 팀으로 넘어가고,"
        " 4단계 시간표도 같은 시각으로 그립니다."
    )
    if int(per_day) > len(SCHED_HOURS):
        st.warning(
            f"4단계 시간표는 한 팀당 하루 {len(SCHED_HOURS)}칸까지만 씁니다 — "
            f"하루 {int(per_day)}명으로 잡으면 뒤의 {int(per_day) - len(SCHED_HOURS)}명은 "
            "다음 날로 밀려, 이 순서표와 4단계 시간표의 날짜가 어긋납니다. "
            f"하루 인원을 {len(SCHED_HOURS)}명 이하로 낮춰 주세요."
        )
    else:
        timing_now = {"start": start.strip(), "minutes": int(minutes),
                      "rest": int(rest)}
        clock_now = hour_clock(timing_now)
        # 앞뒤가 겹치므로 보통은 모든 칸이 어느 한쪽에는 들어간다. 진행 시간을
        # 아주 길게 잡아 14시 뒤에 시작해서 12시 전에 끝나는 칸이 생기면
        # (있을 수 없지만) 그 칸은 '모든타임' 담당자만 맡을 수 있다.
        orphan = [clock_now.get(h, h) for h in SCHED_HOURS
                  if not hour_bands(h, timing_now)]
        both = [clock_now.get(h, h) for h in SCHED_HOURS
                if len(hour_bands(h, timing_now)) == 2]
        st.caption(
            f"이 조건에서 앞타임은 {band_hours_text(BAND_FRONT, timing_now)}, "
            f"뒤타임은 {band_hours_text(BAND_BACK, timing_now)} 입니다. "
            + (f"{' · '.join(both)} 칸은 앞뒤가 겹쳐 양쪽 담당자 모두 맡을 수 "
               "있습니다 — 그래서 점심때가 비지 않습니다."
               if both else "")
            + (f" {' · '.join(orphan)} 칸은 어느 쪽에도 안 들어가 '모든타임' 담당자 "
               "몫입니다." if orphan else "")
        )
    team_cols = [c for c in table.columns if c != "구분"]
    degree_by_name = {
        (row.get("name") or ""): (row.get("degree_full") or row.get("degree"))
        for rows in rosters.values() for row in rows
    }
    slots: list[str] = []
    cells: dict[tuple, str] = {}
    for _, line in table.iterrows():
        head = str(line["구분"])
        if head.startswith("──"):          # 하루가 끝나면 그때까지 모은 칸을 깐다
            if slots:
                time_grid(team_cols, slots, cells)
            slots, cells = [], {}
            day_title(head)
            continue
        if not head.strip():
            continue
        slots.append(head)
        for team in team_cols:
            text = str(line[team]).strip()
            if not text:
                continue
            name = text.split(" (")[0]
            degree = degree_by_name.get(name) or (
                text.split(" (")[1].rstrip(")") if " (" in text else ""
            )
            cells[(team, head)] = card(
                "", name, "", team=team, degree=degree,
                # 위에서 고른 중복면접자는 순서표에서도 같은 빨간 테두리로 잇는다
                tone="pick" if name and name == dup_label.get(focus, "") else "",
                badge="중복면접" if name in dup_names else "")
    if slots:
        time_grid(team_cols, slots, cells)
    st.caption("카드 색은 부서, 위쪽 띠줄은 학력(박사 · 석사 · 학사)입니다."
               + (f" 빨간 테두리는 지금 짚어 둔 {dup_label[focus]} 님입니다."
                  if focus else ""))
    with st.expander("표로 보기"):
        st.dataframe(
            table, width="stretch", hide_index=True,
            height=min(38 * (len(table) + 1) + 3, 760),
        )
    d1, d2 = st.columns(2)
    d1.download_button(
        "⬇ 면접 순서표 CSV", table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"면접순서_{round_id}.csv", mime="text/csv", key="r_csv",
    )
    d2.download_button(
        "⬇ 면접 순서표 XLSX",
        to_excel({"면접순서": table,
                  "팀별명단": st.session_state.get("roster_matrix", matrix)}),
        file_name=f"면접순서_{round_id}.xlsx", mime=XLSX_MIME, key="r_xlsx",
    )


def render_distribution() -> None:
    st.header("2단계 · 팀별 명단 나누기")
    st.caption(
        "1단계에서 확정한 명단을 팀별로 나눕니다. 서류 합격 여부와 소속 조직을 먼저 "
        "거른 뒤, 팀이 필요로 하는 직무·전공과 학사/대학원 비율에 맞춰 사람을 붙이고, "
        "두 팀이 같이 보고 싶어 하는 지원자는 따로 표시합니다."
    )
    if not need_round():
        return
    sync_round()

    history, err = fetch_json(f"{VERSION_MANAGER}/api/v1/versions/{round_id}/history")
    if err:
        st.error(err)
        return
    masters = [v for v in (history or []) if v.get("kind") == KIND_MASTER]
    if not masters:
        st.warning("이번 회차에 확정 명단이 없습니다. 1단계에서 엑셀을 먼저 올려 주세요.")
        return
    st.caption(f"1단계에서 올린 파일 {len(history or [])}개를 기준으로 나눕니다.")

    plan_now = round_plan_id()
    render_step_guide([
        (bool(plan_now), "①",
         "기준 명단을 고르고 <b>팀별로 나누기</b> 를 누릅니다."
         if not plan_now else "팀이 나뉘었습니다."),
        (bool(plan_now), "②",
         "팀 인원이 넘치거나 모자란 곳을 그 자리에서 옮깁니다 — 옮긴 까닭이 함께 "
         "남습니다."),
        (bool(plan_now), "③",
         "<b>이 배정안으로 확정</b> 을 누른 뒤 <b>3. 부서에 명단 보내기</b> 로 "
         "갑니다."),
    ])

    merged = st.session_state.get("merged_version")
    if merged:
        st.success(
            f"1단계에서 확정한 명단 '{merged.get('file_name')}' "
            f"({merged.get('applicant_count')}명)으로 시작합니다."
        )
    else:
        st.caption("1단계에서 확정 명단을 만들면 그 명단이 아래 기준 명단으로 잡힙니다.")

    st.subheader("① 팀별 명단 만들기")
    label = {
        v["version_id"]: f"{v.get('file_name') or '이름 없는 파일'} · "
                         f"{v.get('applicant_count')}명 · {str(v.get('created_at'))[:16]}"
        for v in masters
    }
    prefer = st.session_state.get("master_version_id")
    ids = list(label)
    index = ids.index(prefer) if prefer in ids else 0
    master_id = st.selectbox("기준이 되는 지원자 명단", ids, index=index,
                             format_func=lambda v: label[v], key="d_master")

    # 두 길은 서로 배타적이다. 승계는 부서가 이미 나눈 명단을 그대로 옮기고,
    # 재배치는 그 나눔을 버리고 점수로 다시 섞는다 — 어느 쪽이든 결과는 한 곳
    # (배정 결과)에만 남아서, 부서에 나가는 명단도 4단계 시간표도 그것을 따른다.
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**1단계에서 나눈 대로**")
        st.caption(
            "취합파일의 `담당팀` 을 그대로 옮깁니다 — 각 팀이 `희망지원자_팀이름` 으로 "
            "올린 명단이 곧 배정입니다. 두 팀이 같은 사람을 적어 냈으면 두 팀 다 "
            "봅니다(중복면접). 정원도 학사·대학원 비율도 손대지 않습니다. "
            "`담당팀` 이 빈 사람만 규칙대로 나눠 담습니다."
        )
        run_inherit = st.button("🧮 팀 배정하기", type="primary", key="d_plan",
                                width="stretch")
    with c2:
        st.markdown("**규칙대로 새로 섞기**")
        st.caption(
            "1단계 나눔을 버리고 처음부터 다시 나눕니다. 지망 조직 · 직무 · 전공 · "
            "학사와 대학원 비율 · 팀 정원을 따져 한 사람을 한 팀에 붙입니다."
        )
        threshold = st.number_input(
            "같이 볼 기준 점수", 0.0, 1.0, 0.8, 0.05, key="d_thr",
            help="1등 팀 점수 대비 2등 팀 점수의 비율입니다. 0.8 이면 2등 팀이 1등의 "
                 "80% 이상일 때 두 팀이 함께 봅니다 — 낮출수록 중복면접이 늘어납니다.",
        )
        run_shuffle = st.button("🔀 명단 재배치", key="d_shuffle", width="stretch")

    if run_inherit or run_shuffle:
        data, perr = post_json(
            f"{DISTRIBUTOR}/api/v1/distribute/plan",
            {
                "round_id": round_id,
                "master_version_id": master_id,
                "mode": "inherit" if run_inherit else "auto",
                "allow_duplicate": True,
                "duplicate_score_threshold": float(threshold),
                "created_by": actor,
            },
            timeout=120.0,
        )
        if perr:
            st.error(perr)
        else:
            st.session_state["plan_id"] = (data or {}).get("plan_id")
            st.session_state["plan_summary"] = data
            clear_caches()
            st.rerun()

    made = st.session_state.get("plan_summary") or {}
    if made.get("auto_filled"):
        st.info(
            f"`담당팀` 이 비어 있던 {made['auto_filled']}명은 규칙대로 나눠 담았습니다 "
            "— 지망 조직·직무·전공과 팀 정원을 보고 골랐습니다. 배정 사유에 "
            "`AUTO_FILL` 표가 붙어 있어 ③ 에서 가려낼 수 있습니다."
        )
    if made.get("unknown_teams"):
        st.warning(
            "취합파일의 `담당팀` 에 모르는 팀 이름이 있었습니다 — "
            + " · ".join(made["unknown_teams"])
            + ". 그 이름만 적힌 지원자는 담당팀이 빈 사람과 같이 규칙대로 나눠 "
            "담았습니다. 적어 낸 팀으로 보내려면 1단계 파일 이름이 "
            "`희망지원자_팀이름` 형식인지 확인하고 확정 명단을 다시 만들어 주세요."
        )

    plan_id = plan_field("d_plan_id")
    if plan_id:
        st.session_state["plan_id"] = plan_id
    else:
        plan_id = round_plan_id()

    st.divider()
    render_roster_organizer(history or [], plan_id, master_id)
    if not plan_id:
        return

    summary, serr = fetch_json(f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}")
    if serr:
        st.error(serr)
        return
    summary = summary or {}

    st.divider()
    st.subheader("③ 나눈 결과 확인")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("팀에 배정된 인원", summary.get("total_applicants"))
    s2.metric("두 팀이 같이 보는 인원", summary.get("duplicate_count"))
    s3.metric("조건에 안 맞아 빠진 인원", summary.get("filtered_count"))
    s4.metric("상태", say(summary.get("status"), STATUS_LABELS))
    st.caption(
        f"{str(summary.get('created_at') or '')[:16].replace('T', ' ')} · "
        f"{summary.get('created_by') or '-'} 님이 "
        + ("**1단계에서 나눈 대로** 만든 배정입니다 — 취합파일의 `담당팀` 을 그대로 "
           "옮겼습니다."
           if summary.get("mode") == "inherit" else
           "**규칙대로 새로 섞어** 만든 배정입니다 — 1단계 팀 나눔은 쓰지 않았습니다.")
    )

    team_counts = summary.get("team_counts") or {}
    if team_counts:
        counts = pd.Series(team_counts, name="배정 인원").sort_index().to_frame()
        profiles, _ = fetch_json(f"{DISTRIBUTOR}/api/v1/profiles")
        if profiles:
            target = {p["team_name"]: p.get("target_headcount") for p in profiles}
            counts["정원"] = [target.get(team) for team in counts.index]
        st.dataframe(counts, width="stretch")
        st.bar_chart(counts, height=280)
        if summary.get("duplicate_count"):
            st.caption(
                f"'배정 인원' 은 그 팀이 주로 맡는 사람 수입니다 — 두 팀이 같이 보는 "
                f"{summary['duplicate_count']}명은 상대 팀 쪽에는 세지 않았으므로, "
                "아래 명단과 팀별 엑셀에는 그만큼 더 들어 있습니다."
            )

    unassigned = summary.get("unassigned") or []
    if unassigned:
        with st.expander(f"어느 팀에도 가지 못한 지원자 {len(unassigned)}명"):
            st.write(", ".join(unassigned[:300]))

    applicants, aerr = fetch_json(f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/applicants")
    if aerr:
        st.error(aerr)
    elif applicants:
        adf = ko_frame(applicants, keep=[
            "team", "name", "applicant_id", "degree_type", "major_final",
            "job_role", "reason_tags",
        ])
        teams = sorted(adf["팀"].dropna().unique()) if "팀" in adf else []
        pick_teams = st.multiselect("팀 골라 보기", teams, default=list(teams),
                                    key="d_team_f")
        shown = adf[adf["팀"].isin(pick_teams)] if teams else adf
        st.dataframe(shown, width="stretch", hide_index=True, height=460)
        st.caption(
            f"{len(shown)} / {len(adf)}자리 보는 중 — 두 팀이 같이 보는 사람은 "
            "팀마다 한 줄씩, 두 줄로 들어 있습니다(면접도 두 번입니다)."
        )
        st.download_button(
            "⬇ 확정 명단 CSV", shown.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"확정명단_{round_id}.csv", mime="text/csv", key="d_csv",
        )

        if teams:
            e1, e2 = st.columns([3, 2])
            export_team = e1.selectbox("팀별 엑셀 내려받기", teams, key="d_export")
            blob, eerr = fetch_bytes(
                f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/export/{export_team}"
            )
            e2.write("")
            if eerr:
                e2.error(eerr)
            else:
                e2.download_button(
                    f"⬇ {export_team}.xlsx", blob, file_name=f"{export_team}.xlsx",
                    mime=XLSX_MIME, key="d_export_dl",
                )

    st.divider()
    st.subheader("④ 손보고 확정하기")
    approved = str(summary.get("status") or "").lower() == "approved"
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**사람 옮기기** — 배정을 직접 고칠 때만 씁니다")
        a1, a2, a3 = st.columns(3)
        move_id = a1.text_input("지원자 번호", key="d_mv_id").strip()
        move_from = a2.text_input("지금 팀", key="d_mv_from").strip()
        move_to = a3.text_input("옮길 팀", key="d_mv_to").strip()
        reason = st.text_input("왜 옮기나요", key="d_mv_reason").strip()
        if st.button("↔ 옮기기", key="d_adjust"):
            if not (move_id and move_from and move_to):
                st.warning("지원자 번호 · 지금 팀 · 옮길 팀을 모두 채워 주세요.")
            else:
                data, merr = post_json(
                    f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/adjust",
                    {
                        "moves": [{
                            "applicant_id": move_id, "from": move_from,
                            "to": move_to, "reason": reason or None,
                        }],
                        "actor": actor,
                    },
                )
                if merr:
                    st.error(merr)
                else:
                    clear_caches()
                    # 아직 확정 전이면 '조정됨' 으로 돌아와 오른쪽 확정 버튼이 살아나고,
                    # 이미 확정했으면 확정인 채로 남는다 (02 adjust_plan 이 그렇게 둔다).
                    approved = str(data.get("status") or "").lower() == "approved"
                    st.success(
                        f"옮겼습니다 — 지금 상태 {say(data.get('status'), STATUS_LABELS)}"
                    )

    with c2:
        st.markdown("**이대로 확정할까요**")
        if not approved:
            st.caption(
                "확정해야 3단계에서 부서로 명단을 보낼 수 있습니다 — 확정 전에는 "
                "배정이 더 바뀔 수 있기 때문입니다."
            )
        # 확정한 배정은 다시 확정할 수도, 되돌릴 수도 없다 (서버가 409 로 막는다).
        # 눌리지 않게 해 두고, 대신 무엇을 하면 되는지 알려 준다.
        if approved:
            when = str(summary.get("approved_at") or "")[:16].replace("T", " ")
            who = summary.get("approved_by")
            st.success(
                "이미 확정했습니다"
                + (f" — {when}" if when else "")
                + (f" · {who} 님" if who else "")
            )
            st.caption(
                "확정한 뒤에는 다시 확정하거나 되돌릴 수 없습니다. 왼쪽 [사람 옮기기] "
                "로는 확정한 뒤에도 고칠 수 있고 확정 상태는 그대로 남습니다 — 아예 "
                "다시 나누려면 ① 에서 새로 배정하세요."
            )
        if st.button("✅ 이대로 확정", type="primary", key="d_approve",
                     disabled=approved):
            _, aerr2 = post_json(
                f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/approve", {"actor": actor}
            )
            if aerr2:
                st.error(aerr2)
            else:
                clear_caches()
                # 다시 그리면 위의 '이미 확정했습니다 — 시각 · 사람' 이 뜬다
                st.rerun()
        reject_reason = st.text_input("다시 하는 이유", key="d_reject_reason",
                                      disabled=approved).strip()
        if st.button("⛔ 다시 하기", key="d_reject", disabled=approved):
            if not reject_reason:
                st.warning("다시 하는 이유를 적어 주세요.")
            else:
                data, rerr = post_json(
                    f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/reject",
                    {"reason": reject_reason},
                )
                if rerr:
                    st.error(rerr)
                else:
                    clear_caches()
                    st.warning(
                        f"되돌렸습니다 — 지금 상태 {say(data.get('status'), STATUS_LABELS)}"
                    )
                    st.rerun()

    with st.expander("팀마다 어떤 사람을 원하는지 (배정 기준 고치기)"):
        profiles, perr2 = fetch_json(f"{DISTRIBUTOR}/api/v1/profiles")
        if perr2:
            st.error(perr2)
        elif profiles:
            st.dataframe(
                ko_frame(profiles, keep=[
                    "team_name", "primary_job", "secondary_job", "preferred_majors",
                    "org_allowed", "grad_ratio_target", "target_headcount",
                    "special_tags",
                ]),
                width="stretch", hide_index=True,
            )
            names = [p["team_name"] for p in profiles]
            target = st.selectbox("고칠 팀", names, key="d_prof_team")
            current = next(p for p in profiles if p["team_name"] == target)
            f1, f2 = st.columns(2)
            primary = f1.text_input("꼭 필요한 직무 (쉼표로 구분)",
                                    ", ".join(current["primary_job"]), key="d_p_primary")
            secondary = f2.text_input("있으면 좋은 직무 (쉼표로 구분)",
                                      ", ".join(current["secondary_job"]),
                                      key="d_p_secondary")
            majors = st.text_input("선호 전공 (쉼표로 구분)",
                                   ", ".join(current["preferred_majors"]),
                                   key="d_p_majors")
            orgs = st.text_input("받을 수 있는 조직 (쉼표로 구분)",
                                 ", ".join(current["org_allowed"]), key="d_p_orgs")
            g1, g2, g3 = st.columns(3)
            ratio = g1.number_input("대학원 출신 비율", 0.0, 1.0,
                                    float(current["grad_ratio_target"]), 0.05,
                                    key="d_p_ratio")
            headcount = g2.number_input("뽑을 인원", 0, 999,
                                        int(current["target_headcount"]), key="d_p_head")
            tags = g3.text_input("특별히 볼 조건 (쉼표로 구분)",
                                 ", ".join(current["special_tags"]), key="d_p_tags")

            def split(text: str) -> list[str]:
                return [t.strip() for t in text.split(",") if t.strip()]

            if st.button("💾 이 팀 기준 저장", key="d_prof_save"):
                _, uerr = put_json(
                    f"{DISTRIBUTOR}/api/v1/profiles/{target}",
                    {
                        "primary_job": split(primary),
                        "secondary_job": split(secondary),
                        "preferred_majors": split(majors),
                        "org_allowed": split(orgs),
                        "grad_ratio_target": float(ratio),
                        "target_headcount": int(headcount),
                        "special_tags": split(tags),
                    },
                )
                if uerr:
                    st.error(uerr)
                else:
                    clear_caches()
                    st.success(f"{target} 기준을 저장했습니다 — 다시 팀 배정을 하면 "
                               "반영됩니다.")


# ============================================================
# 3. 면접 담당자 선별
# ============================================================
SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임",
            "한", "오", "서", "신", "권", "황", "안", "송", "류", "홍"]
GIVEN_1 = ["민", "서", "지", "현", "예", "도", "하", "수", "재", "은",
           "성", "준", "다", "가", "태", "우", "채", "유", "선", "주"]
GIVEN_2 = ["준", "연", "우", "은", "호", "진", "아", "빈", "영", "훈",
           "희", "원", "람", "경", "결", "린", "재", "혁", "슬", "현"]
PER_TEAM_DEFAULT = 6
# 만들어 주는 명단의 직급 — 앞에서부터 리더·중간·막내 순으로 붙인다
TITLES = ["수석", "책임", "책임", "선임", "선임", "사원"]


def title_for(seq: int) -> str:
    return TITLES[min(seq, len(TITLES)) - 1]


def make_team_interviewers(team: str, team_no: int, count: int) -> list[dict]:
    """팀별 가상 면접관 명단 — 팀 이름으로 시드를 고정해 다시 눌러도 같은 사람이 나온다."""
    rng = random.Random(sum(ord(ch) for ch in team) * 131 + count)
    seen: set[str] = set()
    people = []
    for seq in range(1, count + 1):
        while True:
            name = rng.choice(SURNAMES) + rng.choice(GIVEN_1) + rng.choice(GIVEN_2)
            if name not in seen:
                seen.add(name)
                break
        emp_id = f"IVG{team_no}{seq:02d}"
        is_leader = seq == 1
        people.append({
            "사번": emp_id,
            "성명": name,
            "직급": title_for(seq),
            "소속팀": team,
            "이메일": f"{emp_id.lower()}@example.com",
            "일일최대": 4 if is_leader else 6,
            "우선순위": 1 if is_leader else 2,
        })
    return people


def roster_to_xlsx(people: list[dict]) -> bytes:
    frame = pd.DataFrame(
        people,
        columns=["사번", "성명", "직급", "소속팀", "이메일", "일일최대", "우선순위"],
    )
    return to_excel({"면접관명단": frame})


def render_interviewer_generator() -> None:
    """실제 명단이 없어도 팀마다 6명씩 만들어 두고 골라 쓸 수 있게 한다."""
    profiles, _ = fetch_json(f"{DISTRIBUTOR}/api/v1/profiles")
    teams = sorted({p["team_name"] for p in (profiles or []) if p.get("team_name")})
    if not teams:
        roster, _ = fetch_json(f"{SCHEDULER}/api/v1/interviewers")
        teams = sorted({r["team"] for r in (roster or []) if r.get("team")})
    if not teams:
        st.info("팀 정보를 읽지 못했습니다. 2단계에서 팀을 먼저 나누거나 명단을 올려 주세요.")
        return

    c1, c2 = st.columns([3, 1])
    pick = c1.multiselect("만들 팀", teams, default=teams, key="i_gen_teams")
    per_team = c2.number_input("팀마다 몇 명", 1, 20, PER_TEAM_DEFAULT, key="i_gen_n")

    people = [
        person
        for team_no, team in enumerate(pick, start=1)
        for person in make_team_interviewers(team, team_no, int(per_team))
    ]
    if not people:
        return
    preview = pd.DataFrame([
        {
            "소속팀": p["소속팀"], "성명": p["성명"], "직급": p["직급"],
            "역할": role_label(p["우선순위"]), "이메일": p["이메일"],
            "하루 최대": p["일일최대"],
        }
        for p in people
    ])
    st.dataframe(preview, width="stretch", hide_index=True,
                 height=min(38 * (len(people) + 1) + 3, 320))

    g1, g2 = st.columns([1, 2])
    auto_select = g2.checkbox("만들면서 바로 이번 회차 담당자로 정하기", value=True,
                              key="i_gen_auto")
    if not g1.button(f"👥 {len(people)}명 만들기", type="primary", key="i_gen_go"):
        return

    try:
        r = http().post(
            f"{SCHEDULER}/api/v1/interviewers/import",
            files={"file": ("면접관명단_생성.xlsx", roster_to_xlsx(people), XLSX_MIME)},
            data={"actor": actor},
            timeout=60.0,
        )
    except Exception as exc:
        st.error(str(exc))
        return
    if r.status_code >= 300:
        st.error(error_text(r))
        return

    data = unwrap(r) or {}
    clear_caches()
    st.session_state.pop("i_team_f", None)   # 팀 이름이 바뀌었을 수 있다
    message = (f"{data.get('parsed')}명을 등록했습니다 (새로 {data.get('created')}명 · "
               f"고침 {data.get('updated')}명)")
    if auto_select:
        picked, uerr = put_json(
            f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}",
            {"interviewer_ids": [p["사번"] for p in people], "actor": actor},
        )
        if uerr:
            st.error(uerr)
        else:
            clear_caches()
            message += f" · 이번 회차 담당자 {picked.get('selected')}명으로 정했습니다"
    st.success(message)
    st.rerun()


def render_interviewers() -> None:
    st.header("부서 1 · 우리 팀 면접 담당자 정하기")
    st.caption(
        "면접에 들어갈 담당자 명단을 등록하고, 이번 회차에 실제로 들어갈 사람만 "
        "골라 둡니다. 여기서 고른 사람에게만 인사 담당자가 면접자 명단을 보내고, "
        "시간표도 그 사람들로만 짜입니다."
    )
    if not need_round():
        return

    # 아래에서 다시 부르지만 fetch_json 은 캐시를 쓰므로 한 번만 나간다
    have_roster = fetch_json(f"{SCHEDULER}/api/v1/interviewers")[0] or []
    have_picked = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")[0] or []
    answered = [row for row in have_picked if row.get("availability")]
    render_step_guide([
        (bool(have_roster), "①",
         "담당자 명단을 <b>엑셀로 올리기</b> 하거나 <b>팀마다 자동으로 만들기</b> 로 준비합니다."
         if not have_roster else f"담당자 {len(have_roster)}명이 등록돼 있습니다."),
        (bool(have_picked), "②",
         "이번 회차에 들어갈 사람만 체크하고 <b>이 사람들로 정하기</b> 를 누릅니다 — "
         "여기서 고른 사람에게만 인사 담당자가 명단을 보냅니다."
         if not have_picked else f"이번 회차 담당자 {len(have_picked)}명을 정했습니다."),
        (bool(have_picked) and len(answered) == len(have_picked), "③",
         "인사 담당자가 보낸 메일로 <b>가능한 시간</b> 을 적어 냅니다 — "
         f"지금 {len(answered)}/{len(have_picked)}명 회신."),
        (bool((load_handoff(round_id).get("teams") or {})), "④",
         "인사 담당자가 명단을 보내면 <b>2. 면접자 담당자 매칭</b> 으로 갑니다."),
    ])

    st.subheader("① 담당자 명단 준비하기")
    tab_gen, tab_up = st.tabs(["👥 팀마다 자동으로 만들기", "📄 엑셀로 올리기"])

    with tab_gen:
        st.caption(
            "실제 명단이 아직 없을 때, 팀마다 6명씩 연습용 담당자를 만들어 둡니다. "
            "다시 눌러도 같은 사람이 나옵니다."
        )
        render_interviewer_generator()

    with tab_up:
        if INTERVIEWER_SAMPLE.exists():
            st.download_button(
                "⬇ 엑셀 양식 받기", INTERVIEWER_SAMPLE.read_bytes(),
                file_name=INTERVIEWER_SAMPLE.name, mime=XLSX_MIME, key="i_sample",
            )
        upload = st.file_uploader(
            "담당자 명단 엑셀 (사번 · 성명 · 직급 · 소속팀 · 이메일 · 하루 최대 · 역할)",
            type=["xlsx"], key="i_upload",
        )
        if upload is not None and st.button("📥 올리기", type="primary", key="i_import"):
            try:
                r = http().post(
                    f"{SCHEDULER}/api/v1/interviewers/import",
                    files={"file": (upload.name, upload.getvalue(), XLSX_MIME)},
                    data={"actor": actor},
                    timeout=60.0,
                )
            except Exception as exc:
                st.error(str(exc))
            else:
                if r.status_code >= 300:
                    st.error(error_text(r))
                else:
                    data = unwrap(r) or {}
                    clear_caches()
                    # 새 명단의 팀 이름은 예전과 다를 수 있다. 아래 '팀 골라 보기'
                    # 에 남아 있던 옛 팀을 지워, 새 명단 전체가 보이게 한다.
                    st.session_state.pop("i_team_f", None)
                    st.success(
                        f"{data.get('parsed')}명을 등록했습니다 "
                        f"(새로 {data.get('created')}명 · 고침 {data.get('updated')}명) "
                        f"· 팀 {', '.join(data.get('teams') or [])}"
                    )

    st.divider()
    st.subheader("② 이번 회차에 들어갈 담당자")
    roster, rerr = fetch_json(f"{SCHEDULER}/api/v1/interviewers")
    if rerr:
        st.error(rerr)
        return
    roster = roster or []
    if not roster:
        st.info("등록된 담당자가 없습니다. 위에서 명단을 먼저 준비해 주세요.")
        return

    selected, serr = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")
    if serr:
        st.error(serr)
        return
    selected_ids = {row["interviewer_id"] for row in (selected or [])}

    teams = sorted({row["team"] for row in roster if row.get("team")})
    f1, f2 = st.columns([3, 2])
    pick_teams = f1.multiselect("팀 골라 보기", teams, default=list(teams), key="i_team_f")
    preset = f2.selectbox(
        "한꺼번에 고르기", ["(그대로 두기)", "보이는 사람 모두 고르기",
                     "보이는 사람 모두 빼기", "팀장만 고르기"], key="i_preset",
    )

    visible = [row for row in roster if row.get("team") in pick_teams]
    rows = []
    for row in visible:
        checked = row["interviewer_id"] in selected_ids
        if preset == "보이는 사람 모두 고르기":
            checked = True
        elif preset == "보이는 사람 모두 빼기":
            checked = False
        elif preset == "팀장만 고르기":
            checked = row.get("priority") == 1
        band = band_of(row)
        rows.append({
            "면접 참여": checked,
            "성명": row.get("name") or "",
            "직급": row.get("title") or "",
            "소속팀": row.get("team") or "",
            "역할": role_label(row.get("priority")),
            "가능 시간": band,
            "하루 최대": row.get("max_daily"),
            "이메일": row.get("email") or "",
            "사번": row["interviewer_id"],
        })

    # 고른 팀에 아무도 없으면 표가 빈 칸 하나 없이 만들어져, 아래에서 '면접 참여'
    # 칸을 찾다 화면이 죽는다. 명단을 새로 올려 팀 이름이 바뀌었을 때 그렇게 된다.
    if not rows:
        st.info(
            "고른 팀에 담당자가 없습니다 — 위 ‘팀 골라 보기’ 에서 팀을 하나 이상 "
            "골라 주세요."
            + (f" 지금 명단에 있는 팀: {' · '.join(teams)}" if teams else "")
        )
        return

    band_timing = round_timing(load_handoff(round_id))
    st.caption(
        "‘가능 시간’ 은 앞타임 · 뒤타임 · 모든타임 중에서 고릅니다. **어느 날인지는 "
        "묻지 않습니다** — 고른 덩어리를 모든 면접일에 똑같이 적용합니다. 3단계에서 정한 "
        f"면접 진행 조건 기준으로 앞타임은 {band_hours_text(BAND_FRONT, band_timing)}"
        f"({band_cap(BAND_FRONT, band_timing)}칸), "
        f"뒤타임은 {band_hours_text(BAND_BACK, band_timing)}"
        f"({band_cap(BAND_BACK, band_timing)}칸) 입니다. 두 덩어리는 점심때가 비지 "
        "않도록 겹쳐 두었습니다. 여기서 고른 대로만 시간표에 들어가고, 하루 최대 "
        "인원도 그 칸 수로 맞춰집니다."
        + (" 지금 조건에서는 앞타임이 하루 전체를 덮어 ‘모든타임’ 과 같습니다."
           if band_cap(BAND_FRONT, band_timing) >= len(SCHED_HOURS) else "")
    )
    edited = st.data_editor(
        pd.DataFrame(rows),
        width="stretch", hide_index=True, height=520,
        column_config={
            "가능 시간": st.column_config.SelectboxColumn(
                "가능 시간", options=BAND_CHOICES + [BAND_UNSET], required=True,
                help="앞타임(~14시) · 뒤타임(12시~) 을 고르면 그 시간대에만 "
                     "면접이 잡힙니다. 날은 가리지 않습니다",
            ),
        },
        disabled=["성명", "직급", "소속팀", "역할", "하루 최대", "이메일", "사번"],
        key="i_editor",
    )
    picked = edited[edited["면접 참여"]]["사번"].tolist()
    hidden = sorted(selected_ids - {row["interviewer_id"] for row in visible})

    # 화면에서 바꾼 가능 시간만 골라 낸다 (아직 안 정한 사람은 건드리지 않는다)
    before = {row["사번"]: row["가능 시간"] for row in rows}
    bands = {
        str(r["사번"]): str(r["가능 시간"])
        for _i, r in edited.iterrows()
        if str(r["가능 시간"]) in BAND_CHOICES
        and str(r["가능 시간"]) != before.get(str(r["사번"]))
    }

    st.caption(
        f"{len(picked)}명 고름"
        + (f" · 가능 시간 {len(bands)}명 바뀜" if bands else "")
        + (f" · 지금 안 보이는 팀에서 이미 고른 {len(hidden)}명도 그대로 남습니다"
           if hidden else "")
    )
    if st.button("💾 이 사람들로 정하기", type="primary", key="i_save"):
        final = list(dict.fromkeys(picked + hidden))
        if bands:
            _band_result, berr = put_json(
                f"{SCHEDULER}/api/v1/interviewers/bands",
                {"bands": bands, "actor": actor, "timing": band_timing},
            )
            if berr:
                st.error(berr)
                return
        data, uerr = put_json(
            f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}",
            {"interviewer_ids": final, "actor": actor},
        )
        if uerr:
            st.error(uerr)
        else:
            clear_caches()
            st.success(
                f"이번 회차 담당자 {data.get('selected')}명으로 정했습니다."
                + (f" 가능 시간 {len(bands)}명분도 저장했습니다." if bands else "")
            )
            st.rerun()

    if selected:
        with st.expander(f"지금 정해 둔 담당자 {len(selected)}명", expanded=False):
            st.dataframe(
                pd.DataFrame([
                    {
                        "소속팀": row.get("team") or "-",
                        "성명": row.get("name") or row["interviewer_id"],
                        "직급": row.get("title") or "",
                        "역할": role_label(row.get("priority")),
                        "가능 시간": band_of(row),
                        "하루 최대": row.get("max_daily"),
                        "이메일": row.get("email") or "",
                    }
                    for row in selected
                ]),
                width="stretch", hide_index=True,
            )


# ============================================================
# 4. 희망자 취합
# ============================================================
def plan_applicants(plan_id: str) -> tuple[list[dict], str | None]:
    rows, err = fetch_json(f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/applicants")
    return (rows or []), err


def render_send(selected: list[dict]) -> None:
    """팀별 면접자 명단 + 우리 팀 담당자를 부서 뷰어로 보낸다."""
    st.subheader("② 각 팀에 명단 보내기")
    st.caption(
        "여기서 보낸 명단이 현업 부서 화면의 '받은 명단'이 됩니다. 부서가 면접자와 "
        "담당자를 골라 보내 주면 그 결과가 4단계 시간표에 그대로 들어옵니다."
    )
    plan_id = round_plan_id()
    doc = load_handoff(round_id)

    # 보내는 명단의 팀은 배정 결과가 정한다. 2단계에서 다른 나눔으로 차례를 잡아
    # 뒀으면 인사 담당자가 본 명단과 부서가 받는 명단이 달라지므로, 맞추기 전에는
    # 내보내지 않는다.
    applicants, aerr = plan_applicants(plan_id) if plan_id else ([], None)
    want, names = split_map(doc.get("order") or {})
    now, _ = split_map(team_rosters_from_plan(applicants))
    mismatch = render_team_gap(plan_id, team_moves(want, now, names), "c_align")

    # 확정하지 않은 배정은 아직 고치는 중이라는 뜻이다. 그대로 내보내면 부서가 받은
    # 명단과 그 뒤에 고친 배정이 갈라진다 — 팀 어긋남과 함께, 명단이 부서로 나가기
    # 전에 통과해야 하는 두 개의 문이다.
    summary, _ = (fetch_json(f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}")
                  if plan_id else (None, None))
    approved = str((summary or {}).get("status") or "").lower() == "approved"

    if not plan_id:
        st.warning("2단계에서 팀별 명단을 먼저 만들어 주세요.")
    elif not mismatch and not approved:
        st.warning(
            "**2단계 배정을 아직 확정하지 않았습니다.** 2단계 ④ 에서 [이대로 확정] 을 "
            "누르면 보낼 수 있습니다 — 확정 전에는 배정이 더 바뀔 수 있어, 부서가 받아 "
            "둔 명단과 나중 배정이 갈라집니다."
        )

    c1, c2 = st.columns([1, 3])
    if c1.button("📤 명단 보내기", type="primary", key="c_publish",
                 disabled=mismatch or not approved or not plan_id):
        if not selected:
            st.warning("현업 부서 화면에서 면접 담당자를 먼저 정해 주세요.")
        elif aerr:
            st.error(aerr)
        else:
            doc = publish_handoff(round_id, plan_id, applicants, selected, actor)
            st.success(
                f"{len(doc.get('teams') or {})}개 팀에 명단을 보냈습니다 — "
                f"팀마다 면접일({SCHED_DAYS_PER_TEAM}일)도 함께 갔습니다. "
                "어느 팀이나 1일차부터 나란히 봅니다 — 팀끼리 날을 비켜 주지 않습니다. "
                "담당자가 적어 낸 가능 시간(앞타임 · 뒤타임 · 모든타임)은 어느 날에나 "
                "같게 봅니다."
            )
    c2.caption(f"마지막으로 보낸 시각 {str(doc.get('sent_at'))[:16] or '-'}")

    teams = doc.get("teams") or {}
    if not teams:
        st.info("아직 보낸 명단이 없습니다.")
        return
    if doc.get("order"):
        st.caption(
            f"2단계에서 잡은 면접 차례({str(doc.get('order_at'))[:16]})도 함께 갑니다 — "
            "부서 화면에는 사람마다 순번이 붙어 보입니다."
        )
    else:
        st.caption(
            "2단계에서 [면접 순서 잡기]를 누르지 않아 차례 없이 나갑니다 — 부서 화면은 "
            "명단 순서대로 번호를 붙입니다."
        )
    team_colors(teams)
    cards = []
    for team in sorted(teams):
        block = teams[team]
        sub = block.get("submitted") or {}
        pairs = sub.get("pairs") or {}
        cards.append(card(
            f"{team} · 담당자 {len(block.get('interviewers') or [])}명",
            f"면접자 {len(block.get('applicants') or [])}명"
            + (" · 차례 있음" if block.get("order_planned") else ""),
            (f"부서에서 {len(pairs)}명 보내 옴 · {str(sub.get('at'))[:16]}"
             if sub else "부서 회신 기다리는 중"),
            tone="done" if sub else "",
            team=team,
        ))
    card_grid(cards, cols=min(4, len(cards)) or 1)




def render_collection() -> None:
    st.header("3단계 · 부서에 명단 보내기")
    st.caption(
        "각 팀에 면접자 명단을 보내고, 부서가 정해 둔 담당자에게 면접 가능한 시간을 "
        "물어봅니다. 면접 볼 사람을 고르고 짝을 맞추는 일은 현업 부서 화면에서 하며, "
        "그 결과가 4단계 시간표의 재료가 됩니다."
    )
    if not need_round():
        return

    selected, serr = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")
    if serr:
        st.error(serr)
        return
    selected = selected or []

    sent_doc = load_handoff(round_id)
    sent_teams = sent_doc.get("teams") or {}
    replied = [team for team, block in sent_teams.items()
               if (block.get("submitted") or {}).get("pairs")]
    render_step_guide([
        (bool(selected), "①",
         "현업 부서 화면 <b>1. 면접 담당자 정하기</b> 에서 담당자를 고르면 여기 뜹니다."
         if not selected else f"담당자 {len(selected)}명이 정해졌습니다."),
        (bool(sent_teams), "②",
         "<b>명단 보내기</b> 를 누릅니다 — 2단계 배정을 확정해야 눌립니다."
         if not sent_teams else f"{len(sent_teams)}개 팀에 명단이 갔습니다."),
        (any(row.get("availability") for row in selected), "③",
         "<b>가능한 시간 물어보기</b> 로 담당자에게 메일을 보내고 회신을 받습니다."),
        (bool(sent_teams) and len(replied) == len(sent_teams), "④",
         "부서가 <b>2. 면접자 담당자 매칭</b> 을 보내 오면 "
         f"<b>4. 면접 시간표 만들기</b> 로 갑니다 — 지금 {len(replied)}/{len(sent_teams)}개 팀."),
    ])

    st.subheader("① 이번에 연락할 담당자")
    if selected:
        frame = pd.DataFrame([
            {
                "소속팀": row.get("team") or "미상",
                "성명": row.get("name") or row["interviewer_id"],
                "직급": row.get("title") or "",
                "역할": role_label(row.get("priority")),
                "가능 시간": band_of(row),
                "하루 최대": row.get("max_daily"),
                "이메일": row.get("email") or "",
                "회신": "○" if row.get("availability") else "-",
            }
            for row in selected
        ]).sort_values(["소속팀", "역할", "성명"], ascending=[True, True, True])
        st.dataframe(frame, width="stretch", hide_index=True,
                     height=min(38 * (len(frame) + 1) + 3, 400))
        by_team = frame.groupby("소속팀").size()
        st.caption(
            " · ".join(f"{team} {n}명" for team, n in by_team.items())
            + f" · 모두 {len(frame)}명 (부서가 고르지 않은 사람에게는 연락이 "
            "가지 않습니다)"
        )

    st.divider()
    render_send(selected)

    st.divider()
    st.subheader("③ 가능한 시간 물어보기")
    if not selected:
        st.warning("현업 부서 화면에서 이번 회차 면접 담당자를 먼저 정해 주세요.")
    else:
        no_email = [iv_label(row) for row in selected if not row.get("email")]
        if no_email:
            st.warning(f"이메일이 없어 연락하지 못하는 사람: {', '.join(no_email)}")
        invitees = [
            {
                "name": iv_label(row),
                "email": row["email"],
                "team": row.get("team") or "미상",
                "org": row.get("team") or None,
                "dept_leader_email": row["email"] if row.get("priority") == 1 else None,
            }
            for row in selected if row.get("email")
        ]
        c1, c2, c3 = st.columns(3)
        deadline_date = c1.date_input(
            "언제까지 답해 달라고 할까요",
            value=(datetime.now() + timedelta(days=3)).date(), key="c_date",
        )
        deadline_time = c2.time_input(
            "그날 몇 시까지", value=datetime.strptime("18:00", "%H:%M").time(),
            key="c_time",
        )
        plan_id = plan_field("c_plan", c3)

        st.dataframe(
            pd.DataFrame([
                {"소속팀": row["team"], "성명": row["name"], "이메일": row["email"]}
                for row in invitees
            ]),
            width="stretch", hide_index=True, height=260,
        )
        if st.button(f"📨 {len(invitees)}명에게 물어보기", type="primary", key="c_send"):
            if not plan_id:
                st.warning("2단계에서 팀별 명단을 먼저 만들어 주세요.")
            elif not invitees:
                st.warning("연락할 사람이 없습니다.")
            else:
                data, err = post_json(
                    f"{COLLECTOR}/api/v1/requests",
                    {
                        "round_id": round_id,
                        "plan_id": plan_id,
                        "deadline": datetime.combine(deadline_date, deadline_time).isoformat(),
                        "invitees": invitees,
                    },
                    timeout=120.0,
                )
                if err:
                    st.error(err)
                else:
                    st.session_state["request_id"] = data.get("request_id")
                    clear_caches()
                    st.success(f"{data.get('sent_count')}명에게 보냈습니다.")

    st.divider()
    st.subheader("④ 누가 답했는지 보기")
    responses, rerr = fetch_json(f"{COLLECTOR}/api/v1/responses/{round_id}")
    if rerr:
        st.error(rerr)
        return
    responses = responses or {}
    if not responses.get("total"):
        st.info("이번 회차로 아직 보낸 요청이 없습니다.")
        return

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("보낸 사람", responses.get("total"))
    r2.metric("답한 사람", responses.get("responded"))
    r3.metric("아직 안 한 사람", responses.get("pending"))
    r4.metric("답한 비율", f"{round((responses.get('response_rate') or 0) * 100, 1)}%")
    if responses.get("avg_response_hours") is not None:
        st.caption(f"평균 {responses['avg_response_hours']}시간 만에 답이 옵니다.")

    items = responses.get("responses") or []
    rdf = pd.DataFrame([
        {
            "성명": i["name"], "팀": i["team"], "조직": i.get("org"),
            "이메일": i["email"], "답함": "✅" if i["responded"] else "⏳",
            "답한 시각": str(i.get("submitted_at") or "")[:16],
            "걸린 시간(시간)": i.get("response_hours"),
            "재촉 횟수": i.get("last_reminder_level"),
            "적어 낸 시간 수": sum(
                1 for _ in ((i.get("payload") or {}).get("available_slots") or [])
            ),
        }
        for i in items
    ])
    st.dataframe(rdf, width="stretch", hide_index=True, height=380)

    c1, c2 = st.columns([1, 4])
    if c1.button("🔔 안 한 사람에게 다시 알리기", key="c_remind"):
        data, err = post_json(f"{COLLECTOR}/api/v1/reminders/run-cycle", {})
        if err:
            st.error(err)
        else:
            clear_caches()
            st.success(f"{data.get('sent_count')}명에게 다시 알렸습니다.")
    c2.caption("마감 3일 전 · 하루 전 · 마감일에 해당하는 사람에게만 나갑니다 "
               "(마감일에는 팀장도 함께 받습니다).")

    st.divider()
    st.subheader("⑤ 모인 가능 시간")
    summary, sumerr = fetch_json(f"{COLLECTOR}/api/v1/rounds/{round_id}/availability/summary")
    if sumerr:
        st.error(sumerr)
    elif summary:
        s1, s2, s3 = st.columns(3)
        s1.metric("물어본 사람", summary.get("invited"))
        s2.metric("답한 사람", summary.get("responded"))
        s3.metric("모인 가능 시간", summary.get("total_slots"))
        if summary.get("teams"):
            st.dataframe(ko_frame(summary["teams"]), width="stretch", hide_index=True)

    include_pending = st.checkbox("아직 답 안 한 사람도 보기", value=False,
                                  key="c_pending")
    detail, derr = fetch_json(
        f"{COLLECTOR}/api/v1/rounds/{round_id}/availability",
        (("include_pending", str(include_pending).lower()),),
    )
    if derr:
        st.error(derr)
    elif detail:
        ddf = pd.DataFrame([
            {
                "성명": row["name"], "팀": row["team"], "직무": row.get("job_role"),
                "역할": role_label(row["priority"]), "하루 최대": row["max_daily"],
                "가능 시간 수": row["slot_count"],
                "답함": "✅" if row["responded"] else "⏳",
                "가능한 때": ", ".join(
                    f"{day}({'/'.join(hours)})"
                    for day, hours in (row.get("availability") or {}).items()
                ),
            }
            for row in detail
        ])
        st.dataframe(ddf, width="stretch", hide_index=True, height=380)
    else:
        st.info("아직 답이 온 가능 시간이 없습니다.")

    with st.expander("보낸 메일 확인하기"):
        history, herr = fetch_json(
            f"{NOTIFIER}/api/v1/notify/history", (("round_id", round_id), ("limit", 200))
        )
        if herr:
            st.error(herr)
        elif history and history.get("items"):
            st.dataframe(ko_frame(history["items"]), width="stretch", hide_index=True)
        else:
            st.info("보낸 메일이 없습니다.")


# ============================================================
# 5. 면접 일정 분배
# ============================================================
def pair_schedule(applicants: list[dict], pairs: dict, iv_name: dict, *,
                 start: str = "09:00", minutes: int = SLOT_MINUTES,
                 rest: int = BREAK_MINUTES, per_day: int = SLOTS_PER_DAY,
                 balance: bool = True,
                 can: dict[str, dict[int, set[int]]] | None = None,
                 days: list[str] | None = None,
                 unanswered: set[str] | None = None) -> list[dict]:
    """매칭(면접자→담당자)만으로 일자별 시간표를 만든다.

    5번 스케줄러가 아직 돌지 않아도 부서가 제출한 즉시 시간표를 볼 수 있어야 한다.
    가나다순을 기본으로 학력을 섞고, 하루 per_day 칸씩 끊어 일차를 매긴다.

    `can` (사번 → {일차: 맡을 수 있는 칸})을 주면 그 자리에만 놓는다. 담당자가
    적어 낸 덩어리(앞타임 · 뒤타임 · 모든타임)가 그대로 그 칸 목록이고, 어느
    일차든 같다 — 담당자 가능 날이라는 것은 우리 모델에 없다.

    맡을 자리를 못 찾은 사람은 자리는 주되 `off_band` 로 표시하고 왜 그런지
    (`off_why`)를 함께 남긴다 — 자리를 안 주면 부서 화면에서 그 사람이 통째로
    사라져 버려 무엇이 잘못됐는지 볼 수가 없다.

    사유는 셋으로 갈린다. ① 아직 가능 시간을 안 적어 내셨다 ② 적어는 내셨는데
    쓸 수 있는 칸이 하나도 없다('어려움') ③ 그 칸이 이미 다 찼다. 할 일이 서로
    다르다 — ①은 가능 시간을 받아 와야 하고, ②·③은 담당자를 더 불러야 한다.
    """
    rows = [
        {
            "applicant_id": row["applicant_id"],
            "name": row.get("name") or row["applicant_id"],
            "degree": degree_label(row.get("degree_type")),
            "degree_full": degree_full(row.get("degree_type")),
            "interviewer_id": pairs.get(row["applicant_id"]),
            # 인사 담당자가 잡아 보낸 순번 — 있으면 이 차례가 시간표 순서가 된다
            "order": row.get("order"),
        }
        for row in applicants if row["applicant_id"] in pairs
    ]
    labels = slot_labels(start, per_day, minutes, rest)
    every = sorted(range(per_day))
    unanswered = set(unanswered or ())
    # 인사팀이 이 팀에 잡아 준 면접일 수 — 부서가 그보다 긴 시간표를 짜면
    # 최종 시간표가 그 일차를 받아 줄 수 없다.
    limit = len(days or []) or (len(rows) + 1)

    def why_off(interviewer_id) -> str:
        """왜 이분이 이 자리를 못 맡는지 — 할 일이 달라지므로 갈라서 적는다."""
        if interviewer_id in unanswered:
            return "가능 시간을 아직 안 적어 내셨습니다"
        mine = (can or {}).get(interviewer_id) or {}
        if not any(mine.get(day) for day in range(limit)):
            return "적어 내신 가능 시간에는 맡으실 수 있는 칸이 없습니다"
        return "적어 내신 가능 시간의 칸이 이미 다 찼습니다"

    taken: set[tuple[int, int]] = set()
    placed = []
    for row in order_for_interview(rows, balance, per_day):
        mine = (can or {}).get(row["interviewer_id"])
        cell = None
        if mine is not None:
            for day in range(limit):
                ok = sorted(mine.get(day) or ())
                cell = next(((day, s) for s in ok if (day, s) not in taken), None)
                if cell:
                    break
        off = cell is None and mine is not None
        if cell is None:
            for day in range(len(rows) + 1):
                cell = next(((day, s) for s in every if (day, s) not in taken), None)
                if cell:
                    break
        taken.add(cell)
        placed.append((cell, {
            **row,
            "day": cell[0] + 1,
            # 몇 번째 칸인지도 함께 남긴다 — 시각 글자는 진행 조건이 바뀌면
            # 달라지지만 칸 번호는 그대로라, 인사팀에 넘길 때는 이 번호를 쓴다.
            "slot_no": cell[1],
            "slot": labels[cell[1]],
            "off_band": off,
            "off_why": why_off(row["interviewer_id"]) if off else "",
            "interviewer": iv_name.get(row["interviewer_id"],
                                       row["interviewer_id"] or ""),
        }))
    # 가능 시간 때문에 순서가 바뀌었을 수 있으니 시각 순으로 다시 세운다
    return [row for _cell, row in sorted(placed, key=lambda p: p[0])]


def assign_by_availability(
    queue: list[str], order: list[str], caps: dict[str, int],
    can: dict[str, dict[int, set[int]]], per_day: int, day_count: int, lead: str,
) -> tuple[dict[str, str], list[tuple[int, int]]]:
    """면접 차례대로 담당자를 정한다 — 그 사람이 그 **날 그 칸** 에 될 때만.

    `queue` 의 몇 번째냐가 곧 며칠째 몇 번째 자리(= 몇 시)냐다. 그래서 그 자리를
    맡을 수 있는 담당자 중에서만 고른다. 예전에는 앞타임 · 뒤타임 덩어리만 보고
    골랐는데, 덩어리에는 날이 없어서 그날 못 오시는 분에게 자리가 갔다.
    그 짝은 인사팀 시간표에서 그대로 버려졌다.

    맡을 사람이 아무도 없는 자리는 그날 가장 여유 있는 분이 떠안고, 그 자리
    목록을 함께 돌려준다 — 담당자를 더 부르거나 가능 시간을 더 받아야 한다는
    뜻이다. 예전에는 그런 자리를 죄다 팀장에게 얹었는데, 그러면 팀장만 하루
    한도를 크게 넘겨 인사팀 시간표에서 그 자리들이 도로 옮겨졌다. 어차피 못
    채우는 자리라면 한 사람을 무너뜨리지 말고 나눠 지는 편이 뒤가 덜 흔들린다.
    """
    rank = {iid: index for index, iid in enumerate(order)}
    daily: dict[tuple[str, int], int] = {}
    picks: dict[str, str] = {}
    gaps: list[tuple[int, int]] = []
    last: dict[int, str] = {}      # 그날 바로 앞 칸을 맡은 사람
    span = max(1, per_day)
    room = max(1, day_count or 1)
    for index, aid in enumerate(queue):
        # 하루 한도는 그날치로 센다 — 스케줄러도 (담당자, 일차)로 세기 때문이다
        day, slot = min(index // span, room - 1), index % span
        free = [
            iid for iid in order
            if daily.get((iid, day), 0) < caps.get(iid, 0)
            and slot in (can.get(iid, {}).get(day) or ())
        ]
        if free:
            # 앞사람부터 몰아 줘 연달아 보게 하되, 맡을 수 있는 자리가 좁은
            # 분을 먼저 쓴다 — 늦은 칸만 되는 분을 남겨 두면 그 칸을 아무도
            # 못 맡아 결국 팀장에게 몰린다.
            who = min(free, key=lambda iid: (
                len(can[iid].get(day) or ()), rank[iid]))
        else:
            # 바로 앞 칸을 맡은 분이 아직 하루 한도가 남았으면 그분이 이어 받는다.
            # 여유만 보고 고르면 두 분이 한 칸씩 번갈아 앉아, 어차피 옮겨질 자리
            # 때문에 아무도 연달아 보지 못하는 시간표가 나온다.
            prev = last.get(day)
            if prev is not None and daily.get((prev, day), 0) < caps.get(prev, 0):
                who = prev
            else:
                # 그날 여유가 가장 많은 분 — 같은 여유면 그 칸이 가능 시간에 드는
                # 분을 먼저, 그래도 같으면 앞 차례대로. 아무도 없으면 팀장이 진다.
                who = min(
                    order,
                    key=lambda iid: (
                        daily.get((iid, day), 0) - caps.get(iid, 0),
                        0 if can.get(iid, {}).get(day) else 1,
                        rank[iid],
                    ),
                    default=lead,
                )
            gaps.append((day, slot))
        daily[(who, day)] = daily.get((who, day), 0) + 1
        last[day] = who
        picks[aid] = who
    return picks, gaps


def schedule_cards(rows: list[dict], *, by_person: bool = False, team: str = "") -> None:
    """시간표를 스케줄러처럼 — 왼쪽에 시간, 위에 일자(또는 담당자) — 격자로 깐다."""
    if not rows:
        st.info("보여 줄 일정이 없습니다.")
        return
    slots = sorted({r["slot"] for r in rows})
    days = sorted({r["day"] for r in rows})

    if by_person:
        people = sorted({r["interviewer"] for r in rows})
        for day in days:
            day_title(f"── {day}일차 ──")
            cells = {
                (r["interviewer"], r["slot"]): card(
                    "", r["name"], r["slot"],
                    team=team, degree=r.get("degree_full") or r["degree"],
                )
                for r in rows if r["day"] == day
            }
            time_grid(people, slots, cells, heads={})
        return

    cells = {
        (f"{r['day']}일차", r["slot"]): card(
            "", r["name"], f"담당 {r['interviewer']}",
            team=team, degree=r.get("degree_full") or r["degree"],
        )
        for r in rows
    }
    time_grid([f"{d}일차" for d in days], slots, cells, heads={})


def render_timetable(assignments: list[dict]) -> None:
    """팀 × 일차 시간표 — 칸마다 '지원자 (면접관)' 을 적는다.

    시간표를 만들 때 정해진 시간대(09시 · 10시 …)가 있으면 그 시간대를 그대로
    세로 축으로 쓴다. 시간대가 없는 옛 시간표만 30분 간격으로 순서를 매긴다.
    """
    st.markdown("### 🗓️ 면접 시간표 (누가 언제 누구를 만나는지)")
    hours = hour_slots(assignments)
    by_hour = bool(hours) and all(row.get("hour") for row in assignments)
    minutes, rest, per_day = SLOT_MINUTES, BREAK_MINUTES, SLOTS_PER_DAY
    clock: dict[str, str] = {}

    if by_hour:
        # 스케줄러가 정한 것은 '몇째 칸' 이고, 그 칸이 몇 시인지는 2단계에서 정한
        # 면접 진행 조건이 정한다 — 그래야 2단계 순서표와 시각이 같아진다.
        timing = round_timing(load_handoff(round_id))
        clock = hour_clock(timing)
        labels = [clock.get(h, h) for h in hours]
        st.caption(
            f"3단계에서 정한 면접 진행 조건 그대로입니다 — {timing['start']} 부터 "
            f"한 사람당 {timing['minutes']}분 면접에 {timing['rest']}분 휴식으로 "
            f"하루 {len(SCHED_HOURS)}칸이 죽 이어집니다 (점심 시간은 따로 두지 "
            "않습니다) · 왼쪽이 시간, 위가 팀입니다. "
            f"앞타임은 {band_hours_text(BAND_FRONT, timing)}, "
            f"뒤타임은 {band_hours_text(BAND_BACK, timing)} 로 봅니다."
            + (f" 하루 {timing['per_day']}명으로 잡으셨지만 시간표는 한 팀당 하루 "
               f"{len(SCHED_HOURS)}칸까지만 씁니다."
               if timing["per_day"] > len(SCHED_HOURS) else "")
        )
        with st.expander("빈 칸이 왜 생기나요 · 왜 중간 시간에만 잡힌 팀이 있나요"):
            # 앞뒤가 겹치므로 보통은 없다 — 어느 덩어리에도 안 들어가는 칸만.
            orphan = [clock.get(h, h) for h in SCHED_HOURS
                      if not hour_bands(h, timing)]
            st.markdown(
                f"- 한 팀은 되도록 **{SCHED_DAYS_PER_TEAM}일 안에** 몰아서 봅니다 "
                f"(한 팀 {SCHED_DAYS_PER_TEAM}일 × 하루 {len(SCHED_HOURS)}칸 = "
                f"{SCHED_DAYS_PER_TEAM * len(SCHED_HOURS)}명). 그래서 그 며칠은 꽉 "
                "차고 나머지 날은 비어 보입니다.\n"
                f"- {SCHED_DAYS_PER_TEAM * len(SCHED_HOURS)}명을 넘긴 인원은 다른 "
                "날에 한두 명씩 끼워 넣습니다. 이때 **그날 첫 칸은 되도록 새로 "
                "열지 않으므로** 중간 시간에만 한 명 놓인 날이 생깁니다.\n"
                + (f"- {' · '.join(orphan)} 칸은 앞타임(~14시) 에도 뒤타임(12시~) "
                   "에도 안 들어가 '모든타임' 이라고 답한 담당자만 맡을 수 있습니다. "
                   "그래서 그 칸만 유독 비어 보일 수 있습니다.\n" if orphan else "")
                + "- 특정 담당자가 그 시간에만 된다는 뜻은 아닙니다. 가능 시간을 "
                "적지 않은 담당자는 모두 되는 것으로 보고 배정합니다.\n"
                "- 빈 칸을 줄이시려면 3단계에서 팀당 인원을 고르게 맞추시거나, "
                "담당자 조사 시간대를 늘려 주세요."
            )
    else:
        c1, c2, c3, c4 = st.columns(4)
        start = c1.text_input("몇 시부터", value="09:00", key="t_start")
        minutes = c2.number_input("한 명당 면접(분)", 10, 120, SLOT_MINUTES, 5,
                                  key="t_min")
        rest = c3.number_input("사이 쉬는 시간(분)", 0, 60, BREAK_MINUTES, 5,
                               key="t_rest")
        per_day = c4.number_input("하루 몇 명까지", 1, 20, SLOTS_PER_DAY, key="t_perday")
        try:
            labels = slot_labels(start.strip(), int(per_day), int(minutes), int(rest))
        except ValueError:
            st.error("시작 시각은 09:00 처럼 적어 주세요.")
            return

    roster, _ = fetch_json(f"{SCHEDULER}/api/v1/interviewers")
    names = {
        row["interviewer_id"]: row.get("name") or row["interviewer_id"]
        for row in (roster or [])
    }

    # 4번·부서 뷰어에서 확정한 매칭이 있으면 그 담당자를 우선한다 (★ 표시)
    doc = load_handoff(round_id)
    matched: dict[str, str] = handoff_pairs(doc)
    # 부서에 보낸 명단에는 학교유형 원본이 남아 있어 박사·석사까지 갈라 볼 수 있다
    degrees: dict[str, str] = {
        row["applicant_id"]: degree_full(row.get("degree_type"))
        for block in (doc.get("teams") or {}).values()
        for row in (block.get("applicants") or [])
    }

    # 두 팀이 같이 보는 사람은 시간표에 두 줄로 들어온다 — 자리가 둘이기 때문이다.
    # 예전에는 배정 결과가 한 사람을 한 팀에만 붙여서 1단계 팀별 명단을 따로
    # 읽어다 표를 달았는데, 이제는 시간표 자체가 답을 들고 있다. 실제로 두 번
    # 잡힌 사람만 표시된다는 뜻이기도 하다 — 한쪽 팀이 짝을 못 지어 한 번만
    # 잡혔으면 그 사람의 면접은 정말 한 번이다.
    by_applicant: dict[str, set] = {}
    for row in assignments:
        key = row.get("applicant_id") or row.get("applicant_name")
        if key:
            by_applicant.setdefault(key, set()).add(row.get("team") or "미상")
    duplicated = {key for key, owners in by_applicant.items() if len(owners) > 1}

    def dup_key(item: dict) -> str:
        """이 사람이 중복면접이면 그 열쇠(번호 또는 이름)를, 아니면 빈 글자를 준다."""
        for key in (item.get("applicant_id"), item.get("applicant_name")):
            if key and key in duplicated:
                return key
        return ""

    # (팀, 일차) 별로 모아 시간대 순으로 줄을 세운다
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in assignments:
        key = (row.get("team") or "미상", row.get("day") or "미정")
        buckets.setdefault(key, []).append(row)
    for items in buckets.values():
        items.sort(key=lambda r: (hour_rank(r.get("hour")),
                                  r.get("applicant_name") or ""))

    teams = sorted({team for team, _ in buckets})
    team_colors(teams)
    days = sorted({day for _, day in buckets},
                  key=lambda d: DAY_ORDER.index(d) if d in DAY_ORDER else 99)

    # (팀, 일차, 시간칸) → 그 칸에 들어가는 사람들
    placed: dict[tuple[str, str, str], list[dict]] = {}
    overflow: list[dict] = []
    if by_hour:
        for row in assignments:
            hour = str(row.get("hour"))
            placed.setdefault((row.get("team") or "미상", row.get("day") or "미정",
                               clock.get(hour, hour)), []).append(row)
    else:
        for (team, day), items in buckets.items():
            for index, item in enumerate(items):
                if index < len(labels):
                    placed.setdefault((team, day, labels[index]), []).append(item)
                else:
                    overflow.append(item)

    def cell_text(item: dict) -> str:
        fixed = matched.get(item.get("applicant_id"))
        iid = fixed or item.get("interviewer_id")
        return (f"{item.get('applicant_name') or item.get('applicant_id')} "
                f"({names.get(iid, iid)}{'★' if fixed else ''})")

    blank = {team: "" for team in teams}
    rows: list[dict] = []
    for day_index, day in enumerate(days):
        rows.append({"구분": f"── {day} ──", **blank})
        for label in labels:
            row = {"구분": label}
            for team in teams:
                row[team] = " / ".join(
                    cell_text(item) for item in placed.get((team, day, label), [])
                )
            rows.append(row)
        if day_index < len(days) - 1:
            rows.append({"구분": "", **blank})  # 날 사이 빈 줄

    table = pd.DataFrame(rows, columns=["구분"] + teams)
    for day in days:
        day_title(f"── {day} ──")
        cells: dict[tuple, str] = {}
        for team in teams:
            for label in labels:
                items = placed.get((team, day, label)) or []
                if not items:
                    continue
                item = items[0]
                aid = item.get("applicant_id")
                fixed = matched.get(aid)
                iid = fixed or item.get("interviewer_id")
                degree = degrees.get(aid) or item.get("degree") or "미상"
                more = f" 외 {len(items) - 1}명" if len(items) > 1 else ""
                cells[(team, label)] = card(
                    "",
                    (item.get("applicant_name") or aid) + more,
                    f"담당 {names.get(iid, iid)}" + (" ★확정" if fixed else ""),
                    tone="fix" if fixed else "",
                    team=team, degree=degree,
                    badge="중복면접" if dup_key(item) else "",
                )
        time_grid(teams, labels, cells)
    st.caption(
        "카드 색은 부서, 위쪽 띠줄은 학력(박사 · 석사 · 학사)입니다."
        + (f" · 두 팀이 함께 보는 {len(duplicated)}명에는 '중복면접' 표를 달았습니다 "
           "— 그 사람은 이 시간표에 팀마다 한 칸씩, 서로 다른 시각에 잡혀 "
           "있습니다." if duplicated else "")
    )
    if duplicated:
        dup_rows = [row for row in assignments if dup_key(row)]
        with st.expander(f"⚠ 중복면접 {len(duplicated)}명 — 어느 팀들이 겹쳤는지"):
            st.dataframe(
                pd.DataFrame([
                    {
                        "지원자": row.get("applicant_name") or row.get("applicant_id"),
                        "지원자 번호": row.get("applicant_id"),
                        "함께 보려는 팀": ", ".join(sorted(by_applicant[dup_key(row)])),
                        "이 시간표에서 만나는 팀": row.get("team") or "-",
                        "언제": f"{row.get('day') or '-'} "
                                f"{clock.get(str(row.get('hour')), row.get('hour') or '-')}",
                    }
                    for row in sorted(
                        dup_rows,
                        key=lambda r: (r.get("applicant_name") or ""),
                    )
                ]),
                width="stretch", hide_index=True,
            )
            st.caption(
                "1단계에 올라온 팀별 희망 명단에서 두 팀 이상이 함께 적어 낸 "
                "사람들입니다. 배정은 한 팀에만 붙으므로 시간표에도 한 칸만 "
                "잡혀 있습니다 — 나머지 팀도 보시려면 따로 자리를 잡아 주세요."
            )
    with st.expander("표로 보기"):
        st.dataframe(table, width="stretch", hide_index=True,
                     height=min(38 * (len(table) + 1) + 3, 760))
    st.caption(
        (f"시간대 {len(labels)}칸" if by_hour
         else f"한 명당 {int(minutes)}분 면접 + {int(rest)}분 휴식 · "
              f"하루 {int(per_day)}명")
        + f" · {len(days)}일 · {len(teams)}팀 · 모두 {len(assignments)}건"
        + (f" · ★ 부서가 정해 준 짝 {len(matched)}건을 먼저 반영했습니다" if matched else "")
    )
    if overflow:
        with st.expander(f"⚠ 하루 {int(per_day)}명을 넘겨 못 넣은 {len(overflow)}건"):
            st.dataframe(ko_frame(overflow), width="stretch", hide_index=True)
    # 시간표 중간이 뚫려 보이면 왜 그런지 여기서 답한다
    gaps = empty_slot_reasons(assignments, roster or [])
    if gaps:
        with st.expander(f"❓ 중간에 빈 칸 {len(gaps)}개 — 왜 비었는지"):
            st.dataframe(pd.DataFrame(gaps).assign(
                칸=lambda f: f["칸"].map(lambda h: f"{h} ({clock.get(h, h)})")
            ), width="stretch", hide_index=True)
            st.caption(
                "맨 앞 · 맨 뒤의 여백은 세지 않았습니다 — 그 팀 그날 면접이 "
                "거기까지라는 뜻이지 구멍이 아닙니다."
            )
    st.download_button(
        "⬇ 시간표 XLSX", to_excel({"시간표": table}),
        file_name=f"면접시간표_{round_id}.xlsx", mime=XLSX_MIME, key="t_xlsx",
    )


def schedule_vs_pairs(assignments: list[dict], by_team: dict[str, dict[str, str]]) -> tuple:
    """이 시간표가 '지금' 부서가 보낸 짝으로 만든 것인지 견준다.

    (짝이 바뀐 자리, 짝도 없이 들어간 자리, 짝은 있는데 못 들어간 자리)

    자리는 (팀, 지원자)다 — 두 팀이 같이 보는 사람은 자리가 둘이고 팀마다 담당자가
    다르므로 지원자 번호만으로 견주면 한쪽이 늘 '짝이 바뀌었다' 로 나온다.
    """
    placed = {
        (row.get("team"), row.get("applicant_id")): row.get("interviewer_id")
        for row in assignments
    }
    wanted = {
        (team, applicant): interviewer
        for team, mine in (by_team or {}).items()
        for applicant, interviewer in mine.items()
    }
    changed = [seat for seat, iv in placed.items() if seat in wanted and wanted[seat] != iv]
    stranger = [seat for seat in placed if seat not in wanted]
    missing = [seat for seat in wanted if seat not in placed]
    return changed, stranger, missing


def render_schedule_body(sc_id: str, pairs: dict[str, dict[str, str]] | None = None,
                         days: dict | None = None, timing: dict | None = None) -> None:
    """시간표 상세 — 지표 · 분포 · 배정 목록 · 팀별 · 히트맵 · 규칙.

    pairs 를 주면 그 짝으로 만든 시간표가 맞는지 견줘서 먼저 알려 준다. 회차에는
    예전에 만든 시간표도 남아 있어서, 그중 하나를 보면서 '지금 상태' 라고 믿으면
    ② 의 숫자와 어긋난다.
    """
    sched, err = fetch_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}")
    if err:
        st.error(err)
        return
    sched = sched or {}
    rc = sched.get("rule_compliance") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("시간표에 들어간 사람",
              f"{sched.get('total_assigned')} / {sched.get('total_applicants')}")
    c2.metric("자리를 받은 비율", f"{sched.get('coverage_pct')}%")
    c3.metric("꼭 지켜야 할 규칙 위반", sched.get("hard_violations"))
    c4.metric("규칙 지킴 정도", f"{rc.get('overall')}%")
    st.caption(
        f"{str(sched.get('generated_at'))[:16]} 에 만든 시간표 · 상태 "
        f"{say(sched.get('status'), STATUS_LABELS)}"
    )

    assignments = sched.get("assignments") or []

    # 이 시간표가 지금 부서가 보낸 짝으로 만든 것인지 먼저 밝힌다.
    if pairs is not None:
        changed, stranger, missing = schedule_vs_pairs(assignments, pairs)
        seats = sum(len(mine) for mine in pairs.values())
        if changed or stranger:
            st.error(
                f"**이 시간표는 지금 부서가 보낸 짝({seats}건)으로 만든 것이 "
                "아닙니다.** "
                + (f"부서가 정한 담당자와 다르게 들어간 자리 {len(changed)}건 · "
                   if changed else "")
                + (f"짝도 없이 들어간 자리 {len(stranger)}건 · " if stranger else "")
                + "위 ① 에서 '시간표 만들기' 를 다시 눌러 주세요 — 그래야 ② 의 "
                  "'면접 못 보는 사람' 과 이 시간표가 같은 명단을 봅니다."
            )
        elif missing:
            # 짝은 지켰는데 못 들어간 자리 = 그 담당자의 가능한 시간이 모자란 것
            st.warning(
                f"부서에서 보낸 짝 {seats}건 중 {seats - len(missing)}건만 "
                f"들어갔습니다 — {len(missing)}건은 담당자의 가능한 시간이 모자라 넣지 "
                "못했습니다. 3단계에서 그 담당자의 가능한 시간을 더 받아 주세요."
            )
        elif seats:
            st.success(f"부서가 보낸 짝 {seats}건 그대로 만든 시간표입니다.")

    # 짝은 맞아도 중복면접자 둘의 시각이 겹칠 수 있다 — 부서끼리는 서로의
    # 시간표를 못 보기 때문이다. 여기서만 드러나므로 여기서 고칠 수 있어야 한다.
    render_dup_fix(sc_id, assignments, days=days, timing=timing)

    if not assignments:
        st.warning("시간표에 들어간 사람이 없습니다.")
        return

    df = pd.DataFrame(assignments)
    cols = [
        c for c in [
            "applicant_id", "applicant_name", "team", "degree",
            "day", "hour", "interviewer_id", "lock_level", "reason_tags",
        ] if c in df.columns
    ]
    df = df[cols + [c for c in df.columns if c not in cols and c != "assignment_id"]]

    render_timetable(assignments)

    # ---------------- 분포 ----------------
    st.markdown("### 📈 한눈에 보기")

    def counts(col, order=None):
        s = df[col].value_counts()
        if order:
            idx = [v for v in order if v in s.index] + \
                  [v for v in s.index if v not in order]
            s = s.reindex(idx)
        else:
            s = s.sort_index()
        return s.rename("건수").to_frame()

    g1, g2 = st.columns(2)
    if "day" in df:
        g1.markdown("**일차별 인원**")
        g1.bar_chart(counts("day", DAY_ORDER), height=260)
    if "hour" in df:
        g2.markdown("**시간대별 인원**")
        g2.bar_chart(counts("hour"), height=260)

    g3, g4 = st.columns(2)
    if "team" in df:
        g3.markdown("**팀별 인원**")
        g3.bar_chart(counts("team"), height=260, horizontal=True)
    if "degree" in df:
        g4.markdown("**학력별 인원**")
        g4.bar_chart(counts("degree"), height=260, horizontal=True)

    if {"team", "degree"} <= set(df.columns):
        st.markdown("**팀마다 학사·대학원이 고르게 섞였는지**")
        cross = pd.crosstab(df["team"], df["degree"])
        cross["합계"] = cross.sum(axis=1)
        st.dataframe(cross, width="stretch")
        st.bar_chart(pd.crosstab(df["team"], df["degree"]), height=300, stack=False)

    if {"day", "degree"} <= set(df.columns):
        st.markdown("**일차마다 학사·대학원이 고르게 섞였는지**")
        dd = pd.crosstab(df["day"], df["degree"])
        dd = dd.reindex([d for d in DAY_ORDER if d in dd.index])
        st.bar_chart(dd, height=300, stack=False)

    iv_roster, _ = fetch_json(f"{SCHEDULER}/api/v1/interviewers")
    iv_by_id = iv_names(iv_roster)
    if "interviewer_id" in df:
        st.markdown("**담당자마다 몇 명을 보는지 (한쪽으로 몰리지 않았는지)**")
        by_person = (
            df["interviewer_id"].map(lambda i: iv_by_id.get(i, i))
            .value_counts().sort_index().rename("건수").to_frame()
        )
        st.bar_chart(by_person, height=280)

    # ---------------- 전체 배정 목록 ----------------
    st.markdown("### 🗒️ 전체 면접 목록")
    f1, f2, f3 = st.columns(3)
    teams = sorted(df["team"].dropna().unique()) if "team" in df else []
    days = [d for d in DAY_ORDER if "day" in df and d in set(df["day"])]
    degrees = sorted(df["degree"].dropna().unique()) if "degree" in df else []
    pick_teams = f1.multiselect("팀", teams, default=list(teams), key="s_team")
    pick_days = f2.multiselect("일차", days, default=list(days), key="s_day")
    pick_degs = f3.multiselect("학력", degrees, default=list(degrees), key="s_deg")

    shown = df
    if teams:
        shown = shown[shown["team"].isin(pick_teams)]
    if days:
        shown = shown[shown["day"].isin(pick_days)]
    if degrees:
        shown = shown[shown["degree"].isin(pick_degs)]
    sort_cols = [c for c in ["team", "day", "hour"] if c in shown.columns]
    if sort_cols:
        shown = shown.sort_values(sort_cols)

    listed = shown.copy()
    # 목록에도 교시가 아니라 실제 시각을 적는다 (2단계 조건 그대로)
    clock = hour_clock(round_timing(load_handoff(round_id)))
    if clock and "hour" in listed:
        listed["hour"] = listed["hour"].map(lambda h: clock.get(str(h), h))
    if "interviewer_id" in listed:
        listed["interviewer_name"] = listed["interviewer_id"].map(
            lambda i: iv_by_id.get(i, i)
        )
    listed = ko_frame(listed, keep=[
        "team", "day", "hour", "applicant_name", "degree", "interviewer_name",
        "applicant_id", "reason_tags",
    ])
    st.dataframe(listed, width="stretch", height=620, hide_index=True)
    st.caption(f"{len(shown)} / {len(df)}명 보는 중")
    st.download_button(
        "⬇ 면접 목록 CSV",
        listed.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"면접목록_{round_id}.csv",
        mime="text/csv",
        key="s_csv",
    )

    # ---------------- 팀별 시간표 ----------------
    st.markdown("### 👥 팀별 시간표")
    teams_data, terr = fetch_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}/by-team")
    if terr:
        st.error(terr)
    else:
        for team, rows in (teams_data or {}).get("teams", {}).items():
            st.markdown(f"**{team} — {len(rows)}명**")
            tdf = pd.DataFrame(rows)
            if "interviewer_id" in tdf:
                tdf["interviewer_name"] = tdf["interviewer_id"].map(
                    lambda i: iv_by_id.get(i, i)
                )
            sc = [c for c in ["day", "hour"] if c in tdf.columns]
            if sc:
                tdf = tdf.sort_values(sc)
            # 여기도 자리 번호가 아니라 실제 시각으로 (위 목록과 같은 말로 적는다)
            if clock and "hour" in tdf:
                tdf["hour"] = tdf["hour"].map(lambda h: clock.get(str(h), h))
            st.dataframe(
                ko_frame(tdf, keep=["day", "hour", "applicant_name", "degree",
                                    "interviewer_name", "applicant_id",
                                    "reason_tags"]),
                width="stretch", hide_index=True,
                height=min(38 * (len(tdf) + 1) + 3, 700),
            )

    # ---------------- 히트맵 ----------------
    st.markdown("### 🔥 어느 날 · 시간에 면접이 몰렸는지")
    hm, herr = fetch_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}/heatmap")
    if herr:
        st.error(herr)
    else:
        hm = hm or {}
        grid, hours, hdays = hm.get("grid", {}), hm.get("hours", []), hm.get("days", [])
        hdf = pd.DataFrame(
            [[grid.get(d, {}).get(h, 0) for h in hours] for d in hdays],
            index=hdays, columns=[clock.get(str(h), h) for h in hours],
        )
        try:  # 색 그라데이션은 matplotlib 이 있을 때만
            st.dataframe(hdf.style.background_gradient(cmap="Blues", axis=None),
                         width="stretch")
        except ImportError:
            st.dataframe(hdf, width="stretch")
        st.caption("칸의 숫자는 그 일차·시간에 잡힌 면접 건수입니다.")

    # ---------------- 규칙 준수 ----------------
    st.markdown("### 📐 정한 규칙을 얼마나 지켰는지")
    rules, rerr = fetch_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}/rules")
    if rerr:
        st.error(rerr)
    else:
        rules = rules or {}
        labels_rule = {
            "rule1_grad_balance": "학사·대학원 고르게",
            "rule2_team_conflict": "같은 팀 사람과 안 마주치게",
            "rule3_vertical_group": "같은 팀은 붙여서",
            "rule4_first_slot": "첫 시간 배치",
        }
        for col, (key, label) in zip(st.columns(len(labels_rule)), labels_rule.items()):
            col.metric(label, f"{(rules.get(key) or {}).get('score', '-')}%")
        with st.expander("자세한 값 보기"):
            st.json(rules)


def render_excluded(selected: list[dict], plan_id: str) -> None:
    """4번·부서 뷰어에서 선택되지 않은 사람 — 면접자와 담당자 양쪽을 다시 세운다."""
    st.subheader("② 면접 못 보는 사람")
    doc = load_handoff(round_id)
    teams_doc = doc.get("teams") or {}
    # 짝은 팀마다 따로 센다 — 같이 보는 사람은 한 팀에서만 짝이 지어졌을 수 있고,
    # 그때 나머지 한 팀에서는 여전히 '면접 못 보는 사람' 이다.
    by_team = handoff_pairs_by_team(doc)
    if not teams_doc:
        st.info("3단계에서 명단을 보내고 부서가 짝을 지으면 여기에 남는 사람이 보입니다.")
        return

    out_ap, out_iv, stale = [], [], []
    for team, block in sorted(teams_doc.items()):
        used = set(((block.get("submitted") or {}).get("pairs") or {}).values())
        paired = by_team.get(team) or {}
        out_ap += [{**row, "team": team} for row in (block.get("applicants") or [])
                   if row["applicant_id"] not in paired]
        out_iv += [{**row, "team": team} for row in (block.get("interviewers") or [])
                   if row["interviewer_id"] not in used]
        # 부서가 보낸 뒤에 명단이나 담당자가 바뀌어 무효가 된 짝
        stale += [team for a in ((block.get("submitted") or {}).get("pairs") or {})
                  if a not in paired]
    total_ap = sum(len(b.get("applicants") or []) for b in teams_doc.values())
    total_iv = sum(len(b.get("interviewers") or []) for b in teams_doc.values())

    c1, c2 = st.columns(2)
    c1.metric("면접 못 보는 지원자", f"{len(out_ap)} / {total_ap}")
    c2.metric("맡은 사람 없는 담당자", f"{len(out_iv)} / {total_iv}")

    if stale:
        st.warning(
            f"부서가 보낸 짝 {len(stale)}건은 그 뒤에 명단이나 담당자가 바뀌어 "
            f"무효가 되었습니다 ({' · '.join(sorted(set(stale)))}). 이 사람들은 위 "
            "'면접 못 보는 지원자' 에 들어 있고 시간표에도 들어가지 않습니다 — "
            "3단계에서 명단을 다시 보내 부서가 짝을 다시 짓게 해 주세요."
        )

    team_colors(teams_doc)
    st.markdown("**면접 못 보는 지원자**")
    if out_ap:
        card_grid([
            card(row["team"], row.get("name") or row["applicant_id"], "짝 없음",
                 tone="out", team=row["team"],
                 degree=degree_full(row.get("degree_type")))
            for row in sorted(out_ap, key=lambda r: (r["team"], r.get("name") or ""))
        ], cols=5)
    else:
        st.success("모든 지원자에게 담당자가 정해졌습니다.")

    st.markdown("**맡은 사람이 없는 담당자**")
    if out_iv:
        card_grid([
            card(row["team"], iv_label(row), role_label(row.get("priority")),
                 tone="out", team=row["team"])
            for row in sorted(out_iv, key=lambda r: (r["team"], r.get("name") or ""))
        ], cols=5)
    else:
        st.success("정해 둔 담당자 모두에게 면접자가 있습니다.")

    if out_ap or out_iv:
        ap_frame = pd.DataFrame([
            {
                "팀": row["team"], "성명": row.get("name") or row["applicant_id"],
                "지원자 번호": row["applicant_id"],
                "학력": degree_label(row.get("degree_type")),
            }
            for row in out_ap
        ])
        iv_frame = pd.DataFrame([
            {
                "팀": row["team"], "성명": row.get("name") or row["interviewer_id"],
                "역할": role_label(row.get("priority")),
            }
            for row in out_iv
        ])
        with st.expander("표로 보기 · 내려받기"):
            t1, t2 = st.columns(2)
            if not ap_frame.empty:
                t1.dataframe(ap_frame, width="stretch", hide_index=True, height=300)
            if not iv_frame.empty:
                t2.dataframe(iv_frame, width="stretch", hide_index=True, height=300)
            st.download_button(
                "⬇ 명단 내려받기",
                to_excel({"면접못보는지원자": ap_frame, "맡은사람없는담당자": iv_frame}),
                file_name=f"열외인원_{round_id}.xlsx", mime=XLSX_MIME, key="s_out_xlsx",
            )
        st.caption("이 사람들은 시간표에 들어가지 않습니다 — 현업 부서 화면에서 "
                   "짝을 지어 주면 사라집니다. 시간표는 여기서 남은 사람을 빼고 만듭니다.")


def render_scheduling() -> None:
    st.header("4단계 · 면접 시간표 만들기")
    st.caption(
        "2단계에서 나눈 팀별 명단과 담당자들이 적어 낸 가능한 시간으로 시간표를 만듭니다. "
        "지원자가 자기 팀 사람과 마주치지 않도록 자동으로 피해 줍니다."
    )
    if not need_round():
        return

    ready_pairs = handoff_pairs(load_handoff(round_id))
    made_before = [r for r in fetch_rounds(round_id) if r["round_id"] == round_id]
    render_step_guide([
        (bool(ready_pairs), "①",
         "부서가 <b>2. 면접자 담당자 매칭</b> 을 보내와야 만들 수 있습니다 — "
         "3단계에서 회신 상태를 확인하세요."
         if not ready_pairs else f"부서가 보낸 {len(ready_pairs)}명으로 만듭니다."),
        (bool(made_before), "②",
         "<b>시간표 만들기</b> 를 누릅니다 — 부서가 잡아 둔 자리를 그대로 쓰고, "
         "못 쓴 자리는 왜 옮겼는지 바로 아래에 나옵니다."),
        (bool(made_before), "③",
         "<b>이상 없는지 확인</b> 을 누릅니다. 두 팀 면접이 같은 시각인 사람이 있으면 "
         "<b>오류 수정하기</b> 로 그 사람만 다시 앉힙니다."),
        (any(str(r.get("status") or "").lower() in ("locked", "confirmed")
             for r in made_before), "④",
         "빈칸이나 겹침이 없으면 <b>이대로 확정</b> 을 눌러 마칩니다."),
    ])

    st.subheader("① 시간표 만들기")
    c1, c2 = st.columns([4, 1])
    plan_id = plan_field("s_plan", c1)
    c2.write("")
    run = c2.button("▶ 시간표 만들기", type="primary", key="s_generate")
    with st.expander("자세한 설정 (거의 쓸 일 없습니다)"):
        algorithm = st.selectbox("짜는 방식", ["v5", "v4", "v3", "v2", "v1"], key="s_algo",
                                 help="기본값(v5)을 그대로 두시면 됩니다.")

    picked = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")[0] or []
    timing = round_timing(load_handoff(round_id))
    ignore_avail = st.checkbox(
        "담당자 일정 무시하고 배치하기", value=False, key="s_ignore_avail",
        help="담당자가 적어 낸 앞타임 · 뒤타임을 지키지 않고 빈 자리를 먼저 채웁니다. "
             "자리는 다 차지만 담당자 사정과 어긋날 수 있어, 만든 뒤 개별로 "
             "조율해야 합니다.",
    )
    gaps = team_seat_gap(picked, timing)
    if gaps and not ignore_avail:
        st.warning("담당자 가능 시간만으로는 못 채우는 자리가 있습니다.\n\n"
                   + "\n\n".join(f"- {line}" for line in gaps))
        st.caption(
            "사람을 더 넣으시려면 3단계에서 담당자를 추가로 선별하시고, 지금 "
            "그대로 만드시려면 위 '담당자 일정 무시하고 배치하기'를 켜 주세요."
        )
    if ignore_avail:
        st.info("담당자 가능 시간을 무시하고 만듭니다 — 만든 뒤 어긋난 사람은 "
                "따로 조율해 주세요.")
    st.caption(
        f"이번 회차에 정한 면접 담당자 {len(picked)}명"
        + (" — 정해 둔 사람이 없으면 등록된 담당자 전체로 짭니다." if not picked else "")
    )
    sent_by_team = handoff_pairs_by_team(load_handoff(round_id))
    sent = handoff_pairs(load_handoff(round_id))
    sent_seats = handoff_seats_by_team(load_handoff(round_id))
    # 팀별 면접일은 3단계에서 이미 못 박았다. 여기서 다시 뽑지 않고 그대로
    # 넘겨야 부서가 본 '1일차'와 최종 시간표의 날이 같아진다.
    sent_days = handoff_team_days(load_handoff(round_id))
    seats = sum(len(mine) for mine in sent_by_team.values())
    fixed = sum(len(mine) for mine in sent_seats.values())
    if sent:
        st.caption(
            f"부서에서 짝을 지어 보낸 {seats}건으로 만듭니다 — 부서가 정한 담당자는 "
            "그대로 둡니다."
            + (f" (두 팀이 같이 보는 {seats - len(sent)}명은 면접이 두 번입니다.)"
               if seats > len(sent) else "")
        )
        if fixed:
            st.caption(
                f"그중 {fixed}건은 부서가 자기 시간표에서 자리(몇 일차 · 몇 번째 칸)까지 "
                "정해 보냈습니다 — 그 자리를 먼저 지키고, 못 지킨 자리는 만든 뒤에 "
                "왜 옮겼는지 알려 드립니다."
            )
        else:
            st.caption(
                "부서가 자리까지 정해 보낸 건은 없습니다 — 부서에서 다시 보내기 전까지는 "
                "날 · 시각을 여기서 새로 짭니다."
            )
    else:
        st.info("부서에서 아직 짝을 보내지 않았습니다. 부서 화면의 '② 면접자 담당자 매칭'을 마쳐야 시간표를 만들 수 있습니다.")

    if run:
        if not plan_id:
            st.warning("먼저 2단계에서 팀별 명단을 나눠 주세요.")
        else:
            # 부서가 확정해 보낸 짝과 자리를 그대로 넘긴다. 시간표는 이 짝을
            # 지키고, 짝이 없는 사람은 넣지 않는다 — ②의 명단과 ③의 시간표가
            # 같은 근거를 보게 하려는 것이다. 자리까지 넘기므로 부서가 보고 보낸
            # 시간표가 최종 시간표의 출발점이 된다.
            data, err = post_json(
                f"{SCHEDULER}/api/v1/schedules/generate",
                {
                    "round_id": round_id, "plan_id": plan_id,
                    "algorithm": algorithm, "generated_by": actor,
                    "pairs": sent,
                    "pairs_by_team": sent_by_team,
                    "seats_by_team": sent_seats,
                    "days_by_team": sent_days,
                    "constraints": {"ignore_availability": bool(ignore_avail)},
                },
                timeout=180.0,
            )
            if err:
                st.error(err)
            else:
                st.session_state["schedule_id"] = data.get("schedule_id")
                clear_caches()
                st.success("시간표를 만들었습니다. 아래 ③에서 확인하세요.")
                render_seat_moves(data, load_handoff(round_id))
                render_off_band(data.get("off_band"), timing)

    st.divider()
    render_excluded(picked, plan_id)

    st.divider()
    st.subheader("③ 만들어진 시간표 보기")
    rounds = [r for r in fetch_rounds(round_id) if r["round_id"] == round_id]
    sc_id = st.session_state.get("schedule_id")
    if rounds:
        # 회차에는 예전에 만든 시간표도 남아 있다. 지금 부서 짝과 인원이 다른
        # 것은 목록에서부터 그렇다고 적어 둔다 — 골라 놓고 나서야 어긋난 걸
        # 알게 되면 ② 의 숫자와 왜 다른지 헤매게 된다.
        labels = [
            f"{r['at']} 만듦 · 면접자 {r['assigned']}명"
            + ("" if not sent or r["assigned"] == len(sent)
               else f"  ⚠ 지금 부서 짝 {len(sent)}명과 다름")
            for r in rounds
        ]
        index = next((i for i, r in enumerate(rounds) if r["schedule_id"] == sc_id), 0)
        pick = st.selectbox("어느 시간표를 볼까요", range(len(rounds)), index=index,
                            format_func=lambda i: labels[i], key="s_pick")
        sc_id = rounds[pick]["schedule_id"]

    with st.expander("다른 시간표 불러오기 (거의 쓸 일 없습니다)"):
        manual = st.text_input("시간표 번호", value="", key="s_manual",
                               help="비워 두면 위에서 고른 시간표를 봅니다.").strip()
    if manual:
        sc_id = manual

    if not sc_id:
        st.info("아직 만든 시간표가 없습니다. 위 ①에서 '시간표 만들기'를 눌러 주세요.")
        return

    a1, a2, a3 = st.columns([1, 1, 3])
    if a1.button("🧪 이상 없는지 확인", key="s_validate"):
        data, err = post_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}/validate", {})
        if err:
            st.error(err)
        else:
            hard = len(data.get("hard_violations") or [])
            if hard:
                st.warning(f"꼭 지켜야 할 규칙을 어긴 곳 {hard}군데 — 아래 표에서 확인하세요.")
            else:
                st.success("꼭 지켜야 할 규칙은 모두 지켜졌습니다.")
            render_off_band(data.get("off_band"), timing)
    if a2.button("🔒 이대로 확정", key="s_lock"):
        data, err = post_json(
            f"{SCHEDULER}/api/v1/schedules/{sc_id}/lock",
            {"lock_level": "LOCKED", "actor": actor},
        )
        if err:
            st.error(err)
        else:
            clear_caches()
            st.success("확정했습니다. 이제 이 시간표는 함부로 바뀌지 않습니다.")
    a3.caption("확정해 두면 나중에 일정이 바뀌어도 이 배정은 그대로 유지됩니다.")

    render_schedule_body(sc_id, pairs=sent_by_team, days=sent_days, timing=timing)


def dept_todo(team: str, block: dict, doc: dict, selected: list[dict]) -> list[tuple]:
    """이 팀에 온 요청과 남은 할 일 — (급함 여부, 아이콘, 문구, 갈 곳)."""
    applicants = block.get("applicants") or []
    interviewers = block.get("interviewers") or []
    submitted = block.get("submitted") or {}
    pairs, unsent = team_pairs(block)
    mine = [row for row in selected if (row.get("team") or "미상") == team]
    out: list[tuple] = []

    if not mine:
        out.append((True, "👤", "이번 회차 면접 담당자가 아직 없습니다 — 먼저 정해 주세요",
                    DEPT_MENUS[0]))
    if unsent:
        out.append((True, "📮",
                    f"배정한 {len(pairs)}명이 아직 인사 담당자에게 가지 않았습니다 — "
                    "화면 맨 아래에서 보내 주세요", DEPT_MENUS[1]))
    if applicants and not submitted:
        out.append((True, "📥",
                    f"인사 담당자가 면접자 {len(applicants)}명 명단을 보냈습니다 — "
                    "면접 볼 사람을 골라 회신해 주세요", DEPT_MENUS[1]))
    elif applicants and submitted:
        # 회신한 뒤에 명단이 새로 왔으면 다시 봐야 한다
        if str(doc.get("sent_at") or "") > str(submitted.get("at") or ""):
            out.append((True, "🔁", "회신한 뒤에 명단이 새로 왔습니다 — 다시 확인해 주세요",
                        DEPT_MENUS[1]))
        left = len(applicants) - len(pairs)
        if left > 0:
            out.append((False, "🧩", f"담당자가 정해지지 않은 면접자 {left}명이 남았습니다",
                        DEPT_MENUS[1]))
    if not applicants:
        out.append((False, "⏳", "인사 담당자가 아직 면접자 명단을 보내지 않았습니다", ""))

    no_time = [iv_label(row) for row in mine if not row.get("availability")]
    if no_time:
        out.append((True, "🗓️",
                    f"면접 가능한 시간을 아직 안 적은 담당자 {len(no_time)}명 "
                    f"({', '.join(no_time[:4])}{' 외' if len(no_time) > 4 else ''})", ""))

    others: dict[str, set] = {}
    for other, blk in (doc.get("teams") or {}).items():
        for row in blk.get("applicants") or []:
            others.setdefault(row["applicant_id"], set()).add(other)
    dup = [row for row in applicants if len(others.get(row["applicant_id"], ())) > 1]
    if dup:
        out.append((False, "⚠️",
                    f"다른 팀도 같이 보려는 지원자 {len(dup)}명 (중복면접) — 시간이 겹치지 "
                    "않게 확인해 주세요", DEPT_MENUS[1]))
    if not out:
        out.append((False, "✅", "지금 처리할 일이 없습니다 — 회신까지 모두 끝났습니다", ""))
    return out


def dept_alert_bar() -> str:
    """현업 부서 화면 맨 윗줄 — 온 요청과 해야 할 일을 먼저 알린다."""
    if not round_id:
        return ""
    doc = load_handoff(round_id)
    teams_doc = doc.get("teams") or {}
    selected, _ = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")
    selected = selected or []

    teams = sorted(teams_doc) or sorted({(row.get("team") or "미상") for row in selected})
    if not teams:
        st.markdown(
            '<div class="lgnotice"><div class="row wait">'
            '<span class="ic">⏳</span><span>아직 이번 회차로 온 요청이 없습니다. '
            '먼저 우리 팀 면접 담당자를 등록해 두시면 명단이 바로 옵니다.</span>'
            '</div></div>', unsafe_allow_html=True)
        return ""

    # 팀은 여기서 한 번만 고르고, 아래 화면들이 모두 그 팀을 따라간다
    if st.session_state.get("dept_team") not in teams:
        st.session_state["dept_team"] = teams[0]
    team = (st.selectbox("우리 팀", teams, key="dept_team") if len(teams) > 1
            else st.session_state["dept_team"])

    items = dept_todo(team, teams_doc.get(team) or {}, doc, selected)
    rows = "".join(
        f'<div class="row {"urgent" if urgent else "wait"}">'
        f'<span class="ic">{icon}</span><span>{escape(text)}</span>'
        + (f'<span class="go">{escape(goto)}</span>' if goto else "")
        + "</div>"
        for urgent, icon, text, goto in items
    )
    urgent_n = sum(1 for row in items if row[0])
    st.markdown(
        f'<div class="lgnotice"><div class="ttl">'
        f'<span class="dot"></span>{escape(team)} · 요청받은 일'
        + (f'<span class="cnt">지금 할 일 {urgent_n}건</span>' if urgent_n else
           '<span class="cnt done">밀린 일 없음</span>')
        + f"</div>{rows}</div>",
        unsafe_allow_html=True,
    )
    return team


def render_team_view() -> None:
    """부서(면접관) 뷰어 — 받은 명단에서 면접자와 담당자를 골라 제출한다."""
    st.header("부서 2 · 면접자 담당자 매칭")
    st.caption(
        "인사 담당자가 보낸 우리 팀 면접자 명단입니다. 면접 볼 사람을 고르고 누가 "
        "면접을 볼지 정한 뒤 **배정하기**를 누르면 우리 팀 시간표가 바로 만들어집니다. "
        "그 시간표를 확인하고 맨 아래 **인사 담당자에게 보내기**를 눌러야 인사 담당자의 "
        "면접 시간표에 반영됩니다."
    )
    if not need_round():
        return

    doc = load_handoff(round_id)
    teams_doc = doc.get("teams") or {}
    if not teams_doc:
        st.info("아직 받은 명단이 없습니다. 인사 담당자가 3단계에서 명단을 보내면 "
                "여기에 나타납니다.")
        return

    team_colors(teams_doc)
    # 팀은 맨 윗줄 알림에서 이미 골랐다 (여기서 또 묻지 않는다)
    team = st.session_state.get("dept_team")
    if team not in teams_doc:
        team = st.selectbox("우리 팀", sorted(teams_doc), key="tv_team")
    block = teams_doc[team]
    applicants = block.get("applicants") or []
    # 인사 담당자가 잡아 보낸 면접 차례대로 세운다. 순번이 없던 예전 명단은
    # 받은 차례대로 번호를 붙여 화면과 시간표가 어긋나지 않게 한다.
    planned = bool(block.get("order_planned"))
    for index, row in enumerate(applicants, start=1):
        if not row.get("order"):
            row["order"] = index
    applicants = sorted(applicants,
                        key=lambda r: (r.get("order") or 10 ** 6, r.get("name") or ""))
    # 다른 팀도 같은 사람을 보겠다고 했으면 '중복면접' 으로 알려 준다
    shared: dict[str, set] = {}
    for other, blk in teams_doc.items():
        for row in blk.get("applicants") or []:
            shared.setdefault(row["applicant_id"], set()).add(other)
    duplicated = {aid for aid, ts in shared.items() if len(ts) > 1}
    submitted = block.get("submitted") or {}
    # 화면은 배정해 둔 짝을 보여 주고, 인사 담당자에게 간 것은 submitted 뿐이다
    saved, unsent = team_pairs(block)

    # 담당자는 '부서 1 · 우리 팀 면접 담당자 정하기' 에서 이번 회차로 고른 사람만
    # 쓴다. 명단을 받은 뒤에 담당자를 바꿨어도 여기서는 지금 고른 사람이 기준이다.
    chosen, _ = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")
    interviewers = [row for row in (chosen or [])
                    if (row.get("team") or "미상") == team]
    if not interviewers:
        interviewers = block.get("interviewers") or []   # 아직 못 읽으면 받은 명단대로
    iv_name = iv_names(interviewers)
    # 인사 담당자가 명단과 함께 정해 보낸 우리 팀 면접일. 며칟날까지 보는지를
    # 알아야 부서가 그 안에서만 자리를 잡을 수 있다.
    team_days = handoff_team_days(doc).get(team, [])
    st.caption(
        f"받은 시각 {str(doc.get('sent_at'))[:16]} · 보낸 사람 {doc.get('sent_by')} · "
        f"면접 볼 사람 {len(applicants)}명 · 우리 팀 면접 담당자 {len(interviewers)}명"
        + (f" · 우리 팀 면접일 {' · '.join(team_days)}" if team_days else "")
        + (f" · 마지막으로 보낸 때 {str(submitted.get('at'))[:16]} ({submitted.get('by')})"
           if submitted else " · 아직 보내기 전")
    )
    render_step_guide([
        (bool(interviewers), "①",
         "<b>부서 1</b> 에서 이번 회차 담당자를 먼저 정해 주세요."
         if not interviewers else f"우리 팀 담당자 {len(interviewers)}명이 정해졌습니다."),
        (bool(saved), "②",
         "면접 볼 사람을 체크하고 <b>자동 배정</b> 을 누릅니다 — 담당자가 가능하다고 "
         "적어 낸 가능 시간 안에서만 자리가 잡히고, 못 맡긴 칸은 까닭이 나옵니다."
         if not saved else f"면접자 {len(saved)}명에게 담당자를 붙였습니다."),
        (bool(saved), "③",
         "우리 팀 시간표에서 자리를 끌어 옮겨 고칩니다 — 옮긴 자리는 그대로 인사 "
         "담당자에게 갑니다."),
        (bool(submitted) and not unsent, "④",
         "<b>인사 담당자에게 보내기</b> 를 눌러야 인사 담당자의 4단계 시간표에 들어갑니다."
         if not submitted or unsent else
         f"{str(submitted.get('at'))[:16]} 에 보냈습니다."),
    ])
    if unsent:
        st.warning(
            "배정해 둔 내용이 아직 인사 담당자에게 가지 않았습니다 — 아래 시간표를 "
            "확인하고 맨 아래 '인사 담당자에게 보내기'를 눌러 주세요."
        )
    if not applicants or not interviewers:
        st.warning("받은 명단이나 담당자가 비어 있습니다. 인사 담당자에게 다시 보내 달라고 "
                   "요청해 주세요.")
        return

    st.divider()
    st.subheader("① 면접에 들어갈 수 있는 우리 팀 담당자")
    st.caption(
        "'부서 1 · 우리 팀 면접 담당자 정하기' 에서 이번 회차로 고른 사람들입니다. "
        "아래에서 짝을 지을 때도 이 사람들 중에서만 고를 수 있습니다."
    )
    ap_name = {row["applicant_id"]: row.get("name") or row["applicant_id"]
               for row in applicants}
    already = Counter(iid for aid, iid in saved.items() if iid in iv_name)
    card_grid([
        card(
            f"{role_label(row.get('priority'))} · 맡은 사람 "
            f"{already.get(row['interviewer_id'], 0)}/{row.get('max_daily') or '-'}",
            iv_label(row),
            ", ".join(sorted(ap_name[a] for a, i in saved.items()
                             if i == row["interviewer_id"] and a in ap_name))
            or "아직 맡은 사람 없음",
            tone="done" if already.get(row["interviewer_id"]) else "empty",
            team=team,
        )
        for row in interviewers
    ], cols=3)

    st.divider()
    st.subheader("② 면접 볼 사람 고르고, 담당자 정하기")
    st.caption(
        "카드 왼쪽 위 번호는 인사 담당자가 2단계에서 잡아 보낸 **면접 차례**입니다. "
        "아래 시간표와 자동배치도 이 차례를 그대로 따릅니다."
        if planned else
        "인사 담당자가 2단계에서 잡은 차례가 지금 명단과 달라, 같은 방식(날짜마다 "
        "학력 고르게 · 하루 안에서는 박사 → 석사 → 학사)으로 차례를 다시 잡았습니다."
    )
    tv_timing = round_timing(load_handoff(round_id))
    for line in team_seat_gap(interviewers, tv_timing):
        st.warning(
            line + "\n\n채우시려면 담당자를 더 넣어 주시고, 지금 담당자로 "
            "그대로 진행하시려면 아래 '담당자 일정 무시하고 배치하기'를 켜 주세요 "
            "— 인사팀이 시간표를 만들 때 가능 시간을 지키지 않고 자리부터 채웁니다."
        )
    # 가능 시간을 아직 안 적어 낸 분은 인사팀 시간표에서 '되는 시간이 하나도 없는
    # 사람' 으로 읽힌다. 여기서 아무 때나 되는 것처럼 자리를 맡기면 그 짝은 최종
    # 시간표에서 통째로 빠진다 — 그래서 먼저 짚어 드린다.
    blank = [row for row in interviewers if not iv_answered(row)]
    if blank:
        st.warning(
            f"{' · '.join(iv_label(row) for row in blank)} 님은 가능 시간을 아직 "
            "안 적어 내셨습니다 — 이대로 자리를 맡기면 인사 담당자의 최종 시간표에서 "
            "빠집니다. 인사 담당자에게 3단계 '가능 시간 받기'를 요청하시거나, "
            "아래 '담당자 일정 무시하고 배치하기'를 켜 주세요."
        )
    ignore_band = st.checkbox(
        "담당자 일정 무시하고 배치하기", value=False, key=f"tv_ignore_{team}",
        help="적어 내신 가능 시간을 따지지 않고 하루 최대 인원까지 나눕니다. "
             "아래 ③ 시간표도 함께 무시하고 그립니다.",
    )
    auto = st.session_state.get("tv_auto")
    if auto and auto[0] == team:
        st.success(
            f"{auto[1]}명을 담당자별로 연달아 볼 수 있게 묶어 나눴습니다 — "
            "각자 적어 내신 가능 시간 안에서만 잡았습니다."
            + (f" 그 자리를 맡을 수 있는 담당자가 없는 칸이 {auto[2]}개라 "
               f"그날 여유 있는 분께 나눠 얹었으니 확인해 주세요 — {auto[3]}. "
               "담당자가 더 필요하거나, '담당자 일정 무시하고 배치하기'를 "
               "켜셔야 합니다."
               if auto[2] else "")
        )
        st.session_state.pop("tv_auto", None)
    b1, b2, b3, b4 = st.columns([1, 1, 1.4, 2.6])
    if b1.button("전체 고르기", key="tv_all"):
        for row in applicants:
            st.session_state[f"tv_on_{team}_{row['applicant_id']}"] = True
        st.rerun()
    if b2.button("전체 지우기", key="tv_none"):
        for row in applicants:
            st.session_state[f"tv_on_{team}_{row['applicant_id']}"] = False
        st.rerun()
    if b3.button("🎯 담당자 자동배치하기", key="tv_auto_go"):
        # 체크한 사람만 나눈다. 아무도 안 골랐으면 전체를 고른 것으로 본다.
        targets = [row["applicant_id"] for row in applicants
                   if st.session_state.get(f"tv_on_{team}_{row['applicant_id']}")]
        if not targets:
            targets = [row["applicant_id"] for row in applicants]
        for aid in targets:
            # 화면을 다시 그릴 때 체크가 풀리지 않게 여기서 한 번 더 적어 둔다
            st.session_state[f"tv_on_{team}_{aid}"] = True
        # 한 사람씩 돌아가며 나누면 면접관이 첫 칸 한 건 · 여섯째 칸 한 건처럼
        # 띄엄띄엄 앉게 된다. 그래서 명단 순서대로 한 사람에게 몰아서 채운 다음
        # 담당자로 넘어간다 — 그러면 각자 연달아 보고 끝낸다.
        tv_per_day = max(1, min(20, int(
            st.session_state.get("tv_perday") or tv_timing.get("per_day")
            or SLOTS_PER_DAY
        )))
        # 며칠째 몇 번째 칸인지를 그대로 따진다 — 하루 한도도 그날치로 센다.
        # 예전에는 '하루 한도 × 날 수' 를 총원으로 삼고 가능 시간을 안 봤는데,
        # 그러면 그 칸에 못 오시는 분에게 자리가 가고 인사팀 시간표가 그 짝을 버렸다.
        caps = {
            row["interviewer_id"]: max(0, min(
                int(row.get("max_daily") or len(SCHED_HOURS)), len(SCHED_HOURS)))
            for row in interviewers
        }
        can, blank_ids = team_open_slots(
            interviewers, team_days, tv_per_day, ignore_band)
        order = [row["interviewer_id"] for row in sorted(
            interviewers, key=lambda r: (r.get("priority") != 1,
                                         r.get("interviewer_id") or ""))]
        lead = next((row["interviewer_id"] for row in interviewers
                     if row.get("priority") == 1), interviewers[0]["interviewer_id"])
        # 명단 순서는 시간표에 놓이는 순서와 같게 맞춘다 (아래 ③ 시간표와 동일)
        by_id = {row["applicant_id"]: row for row in applicants}
        queue = [row["applicant_id"] for row in order_for_interview(
            [
                {
                    "applicant_id": aid,
                    "name": by_id[aid].get("name") or aid,
                    "degree_full": degree_full(by_id[aid].get("degree_type")),
                    # 인사 담당자가 보낸 차례가 있으면 그 순서로 묶는다
                    "order": by_id[aid].get("order"),
                }
                for aid in targets
            ],
            True, tv_per_day,
        )]

        picks, gaps = assign_by_availability(
            queue, order, caps, can, tv_per_day, len(team_days), lead)
        for aid, who in picks.items():
            st.session_state[f"tv_iv_{team}_{aid}"] = who
        # 못 맡긴 칸이 어디였는지 사람 말로 적어 둔다 — '몇 개' 만으로는 무엇을
        # 고쳐야 할지 알 수 없다.
        clock = slot_labels(tv_timing["start"], tv_per_day,
                            tv_timing["minutes"], tv_timing["rest"])
        where = " · ".join(sorted({
            f"{(team_days[day] if day < len(team_days) else day + 1)} "
            f"{clock[slot] if slot < len(clock) else slot + 1}"
            for day, slot in gaps
        }))
        if blank_ids and not ignore_band:
            where += (f" (가능 시간을 안 적어 내신 담당자 {len(blank_ids)}명은 "
                      "자리를 못 맡습니다)")
        st.session_state["tv_auto"] = (team, len(targets), len(gaps), where)
        st.rerun()
    b4.caption("체크한 사람만 보내집니다. 자동배치는 한 담당자가 연달아 보도록 "
               "묶되, 그분이 **적어 내신 시간에 드는 자리만** 맡깁니다 — "
               "담당자를 따로 고르지 않으면 팀장이 맡습니다.")

    default_iv = next((row["interviewer_id"] for row in interviewers
                       if row.get("priority") == 1), interviewers[0]["interviewer_id"])
    picked: dict[str, str] = {}
    columns = st.columns(4)
    for index, row in enumerate(applicants):
        aid = row["applicant_id"]
        degree = degree_full(row.get("degree_type"))
        with columns[index % 4].container(border=True):
            st.markdown(degree_chip(degree), unsafe_allow_html=True)
            st.markdown(
                f":blue-badge[{row.get('order')}번] **{row.get('name') or aid}**"
                + ("  :red-badge[중복면접]" if aid in duplicated else "")
            )
            # 인사 담당자가 보낸 초안 차례 — 며칠차 몇 시에 보기로 한 사람인지
            draft_slot = " · ".join(
                part for part in (
                    f"{row['order_day']}일차" if row.get("order_day") else "",
                    row.get("order_time") or "",
                ) if part
            )
            st.caption(f"{degree} · {row.get('major_final') or '-'}"
                       + (f" · 초안 {draft_slot}" if draft_slot else ""))
            # 처음 그릴 때만 보낸 내용대로 맞춰 둔다 — 그 뒤로는 화면에서 고른 값이 기준
            on_key, iv_key = f"tv_on_{team}_{aid}", f"tv_iv_{team}_{aid}"
            st.session_state.setdefault(on_key, aid in saved)
            want = saved.get(aid, default_iv)
            st.session_state.setdefault(
                iv_key, want if want in iv_name else default_iv)
            if st.session_state[iv_key] not in iv_name:
                st.session_state[iv_key] = default_iv   # 담당자가 빠졌으면 팀장으로
            on = st.checkbox("면접 보기", key=on_key)
            who = st.selectbox(
                "면접 담당자", list(iv_name), key=iv_key,
                format_func=lambda i: iv_name[i], label_visibility="collapsed",
            )
            if on:
                picked[aid] = who

    st.divider()
    s1, s2 = st.columns([1, 3])
    if s1.button(f"🧩 배정하기 ({len(picked)}명)", type="primary", key="tv_submit"):
        assign_team(round_id, team, picked, actor)
        st.session_state["tv_assigned"] = team
        st.session_state.pop("tv_done", None)
        st.rerun()
    s2.caption("배정하면 아래에 우리 팀 시간표가 만들어집니다. 아직 인사 담당자에게는 "
               "가지 않습니다 — 확인한 뒤 맨 아래에서 보내 주세요.")
    if st.session_state.get("tv_assigned") == team:
        st.success("배정했습니다 — 아래 시간표를 확인해 주세요.")
        st.session_state.pop("tv_assigned", None)
    if st.session_state.get("tv_done") == team:
        st.success("인사 담당자에게 보냈습니다 — 아래가 이번에 정한 최종 일정입니다.")

    # 짝이 없는 사람은 인사 담당자 시간표에서 '면접 못 보는 사람'이 되므로 여기서 짚어 준다
    left = [(row["applicant_id"], ap_name[row["applicant_id"]],
             degree_full(row.get("degree_type")))
            for row in applicants if row["applicant_id"] not in saved]
    if left:
        st.markdown("**아직 담당자가 정해지지 않은 면접자**")
        card_grid([
            card(team, name, "짝 없음", tone="out", team=team, degree=degree)
            for _, name, degree in sorted(left, key=lambda x: x[1])
        ], cols=5)

    # 같이 보는 사람은 팀마다 한 자리씩이므로 짝도 팀마다 센다
    paired = sum(len(mine) for mine in handoff_pairs_by_team(doc).values())
    total_ap = sum(len(b.get("applicants") or []) for b in teams_doc.values())
    st.caption(
        f"우리 팀 {len(saved)}/{len(applicants)}명 · 전체 회차로는 "
        f"{paired}/{total_ap}명에게 담당자가 정해졌습니다 — 짝이 없는 사람은 "
        "인사 담당자 시간표에서 '면접 못 보는 사람'으로 다시 보여 드립니다."
    )

    pairs = dict(saved)
    if not pairs:
        st.info("아직 배정한 내용이 없습니다 — 위에서 면접 볼 사람을 고르고 배정하기를 "
                "눌러 주세요.")
        # 앞서 보낸 게 있는데 이번에 아무도 안 골랐다면, 그 '없앰' 도 보낼 수 있어야 한다
        if submitted.get("pairs"):
            st.divider()
            st.subheader("⑤ 인사 담당자에게 보내기")
            e1, e2 = st.columns([1.2, 2.8])
            if e1.button("📮 인사 담당자에게 보내기 (0명)", type="primary",
                         key="tv_send_hr"):
                submit_team(round_id, team, {}, actor)
                st.session_state["tv_done"] = team
                st.rerun()
            e2.caption(
                f"지금까지 보낸 {len(submitted['pairs'])}명을 모두 없앱니다 — "
                "인사 담당자 시간표에서 우리 팀 고정 매칭이 빠집니다."
            )
        return

    st.divider()
    st.subheader("③ 우리 팀 면접 시간표")
    # 인사 담당자가 2단계에서 정한 조건을 그대로 띄운다 — 부서가 따로 손대지
    # 않으면 순서표와 같은 시각이 나온다
    timing = round_timing(doc)
    c1, c2, c3, c4 = st.columns(4)
    start = c1.text_input("면접 시작 시각", value=timing["start"], key="tv_start")
    minutes = c2.number_input("한 사람당 면접 시간(분)", 10, 120, timing["minutes"], 5,
                              key="tv_min")
    rest = c3.number_input("사이 쉬는 시간(분)", 0, 60, timing["rest"], 5, key="tv_rest")
    per_day = c4.number_input("하루에 볼 인원", 1, 20, timing["per_day"], key="tv_perday")
    # 담당자가 적어 낸 **가능 시간**(앞타임 · 뒤타임 · 모든타임)을 여기서도 그대로
    # 지킨다 — 부서가 보는 시간표와 인사팀 최종 시간표가 서로 달라지지 않게.
    # 날은 보지 않는다(덩어리는 어느 날에나 같다). '일정 무시' 를 켜 두셨으면
    # 함께 무시한다.
    tv_can, tv_blank = team_open_slots(
        interviewers, team_days, int(per_day),
        bool(st.session_state.get(f"tv_ignore_{team}")))
    try:
        rows = pair_schedule(
            applicants, pairs, iv_name, start=start.strip(), minutes=int(minutes),
            rest=int(rest), per_day=int(per_day), can=tv_can, days=team_days,
            unanswered=set(tv_blank),
        )
    except ValueError:
        st.error("시작 시각은 HH:MM 형식으로 입력하세요. (예: 09:00)")
        return
    off = [row for row in rows if row.get("off_band")]
    if off:
        st.warning(
            "아래 분들은 담당자가 그 자리를 맡을 수 없습니다 — 자리는 잡아 두었지만 "
            "인사 담당자의 최종 시간표에서는 옮겨지거나 빠집니다.\n\n"
            + "\n".join(
                f"- {row['name']} → {row['interviewer']} : {row['off_why']}"
                for row in off
            )
            + "\n\n아직 안 적어 내신 분은 인사 담당자에게 가능 시간을 받아 달라고, "
            "칸이 모자란 것이면 '부서 1' 화면에서 담당자를 더 넣어 주세요."
        )

    schedule_cards(rows, team=team)
    st.caption(
        f"{team} · 모두 {len(rows)}명 · 한 사람당 {int(minutes)}분 면접에 "
        f"{int(rest)}분 휴식 · 하루 {int(per_day)}명씩 · {max(r['day'] for r in rows)}일차까지"
        + (" (" + " · ".join(f"{n}일차={day}"
                             for n, day in enumerate(team_days, start=1)) + ")"
           if team_days else "")
        + " · 카드 위쪽 띠줄은 학력(박사 · 석사 · 학사)입니다."
        + (" 순서는 인사 담당자가 2단계에서 잡아 보낸 차례 그대로입니다." if planned else
           " 학력은 날짜마다 고르게 나누되, 하루 안에서는 박사 → 석사 → 학사 순으로 묶습니다.")
    )

    st.subheader("④ 담당자별 면접 일정")
    schedule_cards(rows, by_person=True, team=team)

    frame = pd.DataFrame([
        {"일차": r["day"], "시간": r["slot"], "면접자": r["name"],
         "학력": r.get("degree_full") or r["degree"], "면접 담당자": r["interviewer"]}
        for r in sorted(rows, key=lambda r: (r["day"], r["slot"]))
    ])
    with st.expander("표로 보기 · 내려받기"):
        st.dataframe(frame, width="stretch", hide_index=True, height=420)
        st.download_button(
            "⬇ 우리 팀 시간표 내려받기", to_excel({team: frame}),
            file_name=f"면접시간표_{team}_{round_id}.xlsx", mime=XLSX_MIME,
            key="tv_xlsx",
        )

    # 다른 팀까지 한 번에 — 인사 담당자에게 그대로 넘길 수 있는 짝짓기 표
    match_rows = []
    for tname, tblock in sorted(teams_doc.items()):
        names = iv_names(tblock.get("interviewers") or [])
        tpairs = (tblock.get("submitted") or {}).get("pairs") or {}
        for row in tblock.get("applicants") or []:
            match_rows.append({
                "팀": tname,
                "면접자": row.get("name"),
                "지원자 번호": row["applicant_id"],
                "학력": degree_full(row.get("degree_type")),
                "면접 담당자": names.get(tpairs.get(row["applicant_id"]), ""),
            })
    match_frame = pd.DataFrame(match_rows)
    with st.expander("이번 회차 전체 짝짓기 보기 · 내려받기"):
        st.dataframe(match_frame, width="stretch", hide_index=True, height=360)
        st.download_button(
            "⬇ 짝짓기 결과 XLSX", to_excel({"매칭": match_frame}),
            file_name=f"면접매칭_{round_id}.xlsx", mime=XLSX_MIME, key="tv_match_xlsx",
        )

    # 여기서 보내야 비로소 인사 담당자의 면접 시간표에 들어간다
    st.divider()
    st.subheader("⑤ 인사 담당자에게 보내기")
    sent_pairs = (submitted.get("pairs") or {})
    n1, n2 = st.columns([1.2, 2.8])
    if n1.button(f"📮 인사 담당자에게 보내기 ({len(pairs)}명)", type="primary",
                 key="tv_send_hr", disabled=not pairs):
        # 짝과 함께 위 ③ 에서 보고 있는 자리를 그대로 넘긴다
        submit_team(round_id, team, pairs, actor, seats={
            row["applicant_id"]: {"day": row["day"], "slot": row["slot_no"]}
            for row in rows
        })
        st.session_state["tv_done"] = team
        st.rerun()
    n2.caption(
        "위 시간표의 자리를 그대로 인사 담당자에게 넘깁니다. 인사 담당자의 "
        "'면접 시간표 만들기'는 이 자리를 먼저 지키고, 못 지킬 때만 옮기면서 "
        "그 까닭을 배정 사유에 적습니다"
        + (f" · 지금까지 보낸 것 {len(sent_pairs)}명" if sent_pairs
           else " · 아직 한 번도 보내지 않았습니다")
        + ("  ⚠️ 배정한 내용이 보낸 내용과 다릅니다" if unsent else "")
    )


# ============================================================
# 관리자
# ============================================================
def render_admin() -> None:
    st.header("⚙️ 관리자")
    tabs = st.tabs([
        "🩺 헬스체크", "📜 이벤트 타임라인", "🗃️ DB 조회",
        "📊 KPI 대시보드", "🚀 시나리오 일괄 실행",
    ])

    # ---------------- 헬스체크 ----------------
    with tabs[0]:
        st.subheader("서비스 헬스체크")
        if st.button("🔄 전체 새로고침", type="primary", key="a_health"):
            st.rerun()
        cols = st.columns(4)
        ok_count = 0
        for index, (name, port) in enumerate(SERVICES):
            col = cols[index % 4]
            try:
                r = http().get(f"http://127.0.0.1:{port}/healthz", timeout=0.5)
                if r.status_code == 200:
                    col.success(f"✅ {name}\n\nport {port}")
                    ok_count += 1
                else:
                    col.warning(f"⚠️ {name}\n\nstatus {r.status_code}")
            except Exception:
                col.error(f"❌ {name}\n\nport {port}\nDOWN")
        st.divider()
        st.metric("정상 서비스", f"{ok_count} / {len(SERVICES)}")

    # ---------------- 타임라인 ----------------
    with tabs[1]:
        st.subheader("이벤트 타임라인")
        tl_round = round_selector("a_tl_round")
        if tl_round:
            data, err = fetch_json(
                f"{AUDIT}/api/v1/audit/timeline", (("round_id", tl_round),)
            )
            if err:
                st.error(err)
            else:
                events = data if isinstance(data, list) else (data or {}).get("events", [])
                if not events:
                    st.info("해당 Round의 이벤트가 없습니다.")
                else:
                    df = pd.DataFrame(events)
                    order = [c for c in ["timestamp", "event_type", "producer", "round_id",
                                         "correlation_id", "payload", "event_id"]
                             if c in df.columns]
                    df = df[order + [c for c in df.columns if c not in order]]
                    st.dataframe(df, width="stretch", hide_index=True,
                                 height=min(38 * (len(df) + 1) + 3, 620))
                    st.caption(f"총 {len(events)}건")

    # ---------------- DB ----------------
    with tabs[2]:
        st.subheader("각 서비스 DB 상태")
        db_map = {
            "version-manager":    PROJECT_ROOT / "services" / "01-version-manager" / "version_db.sqlite",
            "distributor":        PROJECT_ROOT / "services" / "02-distributor" / "dist_db.sqlite",
            "response-collector": PROJECT_ROOT / "services" / "03-response-collector" / "resp_db.sqlite",
            "scheduler":          PROJECT_ROOT / "services" / "04-scheduler" / "sched_db.sqlite",
            "repair-engine":      PROJECT_ROOT / "services" / "05-repair-engine" / "repair_db.sqlite",
            "notification-hub":   PROJECT_ROOT / "services" / "06-notification-hub" / "notif_db.sqlite",
            "audit-analytics":    PROJECT_ROOT / "services" / "07-audit-analytics" / "audit_db.sqlite",
        }
        rows = []
        for svc, path in db_map.items():
            if path.exists():
                rows.append({"service": svc, "exists": "✅",
                             "size_KB": f"{path.stat().st_size / 1024:.1f}",
                             "path": str(path)})
            else:
                rows.append({"service": svc, "exists": "❌", "size_KB": "-",
                             "path": str(path)})
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # ---------------- KPI ----------------
    with tabs[3]:
        st.subheader("KPI 대시보드")
        # 07의 dashboard 엔드포인트는 전부 round_id 가 필수다 (없으면 422).
        kpi_round = round_selector("a_kpi_round")
        if kpi_round:
            kpi, err = fetch_json(f"{AUDIT}/api/v1/dashboard/kpi", (("round_id", kpi_round),))
            if err:
                st.error(err)
            elif isinstance(kpi, dict) and kpi:
                keys = list(kpi.keys())
                for start in range(0, len(keys), 4):
                    for col, key in zip(st.columns(4), keys[start:start + 4]):
                        col.metric(key.replace("_", " "), kpi[key])
                st.caption(
                    "총_대상자는 마스터 전체 인원이고, 실제 면접 배정은 팀 정원까지만 이뤄진다. "
                    "회신_완료는 03(회신 수집)을 거치지 않았다면 0이 정상이다."
                )
            else:
                st.info("해당 Round의 KPI 데이터가 없습니다.")

            st.divider()
            st.markdown("### 🏢 조직 응답 통계")
            orgs, err = fetch_json(
                f"{AUDIT}/api/v1/dashboard/organizations", (("round_id", kpi_round),)
            )
            if err:
                st.error(err)
            elif orgs:
                st.dataframe(pd.DataFrame(orgs), width="stretch", hide_index=True)
            else:
                st.info("조직 응답 데이터가 없습니다. (03 회신 수집 전이면 비어 있는 게 정상)")

            st.divider()
            st.markdown("### ⚠ 위험 신호")
            risks, err = fetch_json(f"{AUDIT}/api/v1/dashboard/risks", (("round_id", kpi_round),))
            if err:
                st.error(err)
            elif risks:
                st.dataframe(pd.DataFrame(risks), width="stretch", hide_index=True)
            else:
                st.success("감지된 위험 신호가 없습니다.")

            st.divider()
            st.markdown("### 📄 라운드 리포트")
            rep, err = fetch_json(f"{AUDIT}/api/v1/reports/rounds/{kpi_round}")
            if err:
                st.error(err)
            else:
                rep = rep or {}
                if rep.get("phases"):
                    st.dataframe(pd.DataFrame(rep["phases"]), width="stretch",
                                 hide_index=True)
                with st.expander("원본 JSON"):
                    st.json(rep)

    # ---------------- 시나리오 ----------------
    with tabs[4]:
        render_scenario()


def render_scenario() -> None:
    """1→2→4 를 한 번에 돌리는 스모크 테스트 (개별 메뉴를 안 거치고 검증용)."""
    st.subheader("시나리오 일괄 실행")
    st.caption("메뉴 1~5 를 거치지 않고 파이프라인이 살아 있는지만 빠르게 확인한다.")
    scenario = st.selectbox(
        "시나리오", ["Happy Path (정상 흐름)", "No-show Repair (노쇼 재편성)"], key="sc_kind"
    )
    uploaded = st.file_uploader("마스터 엑셀 업로드 (선택)", type=["xlsx"], key="sc_file")
    if uploaded is None:
        if DEFAULT_MASTER.exists():
            st.caption(f"업로드가 없으면 기본 파일을 사용합니다: `{DEFAULT_MASTER.name}`")
        else:
            st.warning(f"기본 마스터 파일이 없습니다: {DEFAULT_MASTER} — 파일을 업로드하세요.")

    sc_round = st.text_input(
        "Round ID", value=f"R2026-TEST-{int(time.time()) % 1000:03d}", key="sc_round"
    )
    sc_actor = st.text_input("Actor (등록자)", value="test_console", key="sc_actor")

    if not st.button("▶ 실행", type="primary", key="sc_run"):
        return

    if uploaded is not None:
        file_name, file_bytes = uploaded.name, uploaded.getvalue()
    elif DEFAULT_MASTER.exists():
        file_name, file_bytes = DEFAULT_MASTER.name, DEFAULT_MASTER.read_bytes()
    else:
        file_name, file_bytes = None, None

    if not file_bytes:
        st.error("마스터 엑셀 파일이 필요합니다. 파일을 업로드한 뒤 다시 실행하세요.")
        return

    with st.spinner("시나리오 실행 중..."):
        log = st.empty()
        messages: list[str] = []

        def add(msg: str) -> None:
            messages.append(msg)
            log.code("\n".join(messages))

        version_id = plan_id = schedule_id = None
        failed = False

        # --- Step 1: 마스터 버전 등록 (multipart/form-data) ---
        add(f"[1/4] Version Manager - Round {sc_round} 등록 ({file_name})")
        try:
            r = http().post(
                f"{VERSION_MANAGER}/api/v1/versions/register",
                files={"file": (file_name, file_bytes, XLSX_MIME)},
                data={"round_id": sc_round, "kind": "master", "actor": sc_actor},
                timeout=30.0,
            )
            add(f"  → status {r.status_code}")
            if r.status_code < 300:
                data = unwrap(r) or {}
                version_id = data.get("version_id")
                add(f"  version_id: {version_id} / 지원자 {data.get('applicant_count')}명")
            else:
                failed = True
                add(f"  ❌ {error_text(r)}")
        except Exception as e:
            failed = True
            add(f"  ❌ {e}")

        # --- Step 2: 팀 배포 계획 생성 (master_version_id 필수) ---
        add("[2/4] Distributor - 팀 배포 계획 생성")
        if not version_id:
            add("  ⏭ 건너뜀 — 1단계에서 master_version_id 를 얻지 못함")
        else:
            data, err = post_json(
                f"{DISTRIBUTOR}/api/v1/distribute/plan",
                {"round_id": sc_round, "master_version_id": version_id,
                 "created_by": sc_actor},
                timeout=120.0,
            )
            if err:
                failed = True
                add(f"  ❌ {err}")
            else:
                plan_id = data.get("plan_id")
                add(f"  plan_id: {plan_id}")
                add(f"  배정: {data.get('total_applicants')}명 / 팀별 {data.get('team_counts')}")

        # --- Step 3: 시간표 생성 (plan_id 필수) ---
        add("[3/4] Scheduler - 일정 생성")
        if not plan_id:
            add("  ⏭ 건너뜀 — 2단계에서 plan_id 를 얻지 못함")
        else:
            # 그 회차에 부서가 보낸 짝이 있으면 시나리오도 그 짝을 지킨다.
            # 안 그러면 이 자리에서 만든 '전원 배정' 시간표가 회차 목록의
            # 맨 위에 올라가, 4단계 ③ 이 그걸 보여 주며 ② 와 다른 말을 한다.
            body = {"round_id": sc_round, "plan_id": plan_id, "algorithm": "v5",
                    "generated_by": sc_actor}
            sc_doc = load_handoff(sc_round)
            sc_pairs = handoff_pairs(sc_doc)
            sc_by_team = handoff_pairs_by_team(sc_doc)
            sc_days = handoff_team_days(sc_doc)
            if sc_days:
                body["days_by_team"] = sc_days
            if sc_pairs:
                body["pairs"] = sc_pairs
                body["pairs_by_team"] = sc_by_team
                body["seats_by_team"] = handoff_seats_by_team(sc_doc)
                seats = sum(len(mine) for mine in sc_by_team.values())
                add(f"  부서가 보낸 짝 {seats}건으로 만듭니다")
            data, err = post_json(
                f"{SCHEDULER}/api/v1/schedules/generate", body, timeout=180.0,
            )
            if err:
                failed = True
                add(f"  ❌ {err}")
            else:
                schedule_id = data.get("schedule_id")
                add(f"  schedule_id: {schedule_id}")
                add(
                    f"  배정 {data.get('total_assigned')}/{data.get('total_applicants')}"
                    f" (coverage {data.get('coverage_pct')}%,"
                    f" 하드위반 {data.get('hard_violations')})"
                )

        # --- Step 3.5: 노쇼 재편성 ---
        if scenario.startswith("No-show"):
            add("[3.5] Repair Engine - 노쇼 재편성")
            if not schedule_id:
                add("  ⏭ 건너뜀 — schedule_id 없음")
            else:
                try:
                    def report_noshow(ids):
                        return http().post(
                            f"{REPAIR_ENGINE}/api/v1/repair/noshow",
                            json={"round_id": sc_round, "schedule_id": schedule_id,
                                  "noshow_applicant_ids": ids, "reported_by": sc_actor},
                            timeout=30.0,
                        )

                    r = http().get(f"{SCHEDULER}/api/v1/schedules/{schedule_id}", timeout=15.0)
                    assignments = (unwrap(r) or {}).get("assignments", [])
                    noshow_ids = [a["applicant_id"] for a in assignments[:2]]
                    if not noshow_ids:
                        add("  ⏭ 건너뜀 — 배정된 지원자가 없음")
                    else:
                        r = report_noshow(noshow_ids)
                        # USE_MOCK=true 면 Service 05 는 자체 합성 시간표를 쓴다.
                        # 04 의 지원자 ID 가 없으므로, 05 스냅샷의 ID 로 재시도한다.
                        if r.status_code == 404:
                            add("  ↩ 05 mock 시간표에 없는 ID — 05 스냅샷 기준으로 재시도")
                            locks = http().get(
                                f"{REPAIR_ENGINE}/api/v1/repair/locks/{schedule_id}",
                                timeout=15.0,
                            )
                            rows = (unwrap(locks) or {}).get("locks", [])
                            fallback = [row["applicant_id"] for row in rows
                                        if row.get("lock_level") != "LOCKED"][:2]
                            if fallback:
                                noshow_ids = fallback
                                r = report_noshow(noshow_ids)
                        add(f"  → status {r.status_code} (노쇼 {len(noshow_ids)}명: {noshow_ids})")
                        if r.status_code < 300:
                            add(f"  data: {str(unwrap(r))[:200]}")
                        else:
                            failed = True
                            add(f"  ❌ {error_text(r)}")
                except Exception as e:
                    failed = True
                    add(f"  ❌ {e}")

        # --- Step 4: 감사 타임라인 확인 ---
        add("[4/4] Audit - 이벤트 타임라인 확인")
        # 포워딩이 비동기라, 앞 단계가 성공시킨 이벤트가 다 도착할 때까지 기다린다.
        expected = set()
        if version_id:
            expected.add("MASTER_REGISTERED")
        if plan_id:
            expected.add("DISTRIBUTION_PLAN_CREATED")
        if schedule_id:
            expected.add("SCHEDULE_GENERATED")
        try:
            events = []
            for _ in range(20):
                r = http().get(f"{AUDIT}/api/v1/audit/timeline",
                               params={"round_id": sc_round}, timeout=5.0)
                data = unwrap(r)
                events = data if isinstance(data, list) else (data or {}).get("events", [])
                if expected <= {ev.get("event_type") for ev in events}:
                    break
                time.sleep(0.2)
            add(f"  events: {len(events)}")
            for ev in events:
                add(f"    - {ev.get('event_type')} ({ev.get('producer')})")
            missing = expected - {ev.get("event_type") for ev in events}
            if missing:
                add(f"  ⚠ 도착하지 않은 이벤트: {', '.join(sorted(missing))}")
        except Exception as e:
            add(f"  ❌ {e}")

        add("\n❌ 실패한 단계가 있습니다" if failed else "\n✅ 완료")
        st.session_state["plan_id"] = plan_id
        st.session_state["schedule_id"] = schedule_id
        clear_caches()


# ============================================================
# 라우팅
# ============================================================
brand_bar(
    {VIEWERS[1]: dept_menu, VIEWERS[2]: "시스템 관리"}.get(viewer, menu),
    f"{viewer} · {round_id}" if round_id else viewer,
)

if viewer == VIEWERS[1]:
    dept_alert_bar()              # 온 요청과 할 일을 맨 윗줄에 먼저 보여 준다
    if dept_menu == DEPT_MENUS[1]:
        render_team_view()
    else:
        render_interviewers()
elif viewer == VIEWERS[2]:
    render_admin()
elif menu == MENUS[1]:
    render_distribution()
elif menu == MENUS[2]:
    render_collection()
elif menu == MENUS[3]:
    render_scheduling()
else:
    render_versions()
