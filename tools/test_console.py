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
import time
from collections import Counter
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

st.set_page_config(page_title="HR Interview Console", page_icon="🎯", layout="wide")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MASTER = PROJECT_ROOT / "docs" / "취합파일.xlsx"
INTERVIEWER_SAMPLE = PROJECT_ROOT / "tools" / "fixtures" / "면접관명단_sample.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]

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
# 카드 UI — 모든 화면은 시간표처럼 칸(카드)으로 보여 준다
# ============================================================
st.markdown("""
<style>
.hrgrid {display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 14px;}
.hrcard {flex:1 1 200px; min-width:170px; max-width:280px; padding:8px 11px;
         border:1px solid rgba(130,140,160,.35); border-radius:10px;
         background:rgba(130,150,190,.08);}
.hrcard .t {font-size:.74rem; opacity:.6; letter-spacing:.02em;}
.hrcard .h {font-weight:600; font-size:.97rem; margin:1px 0 2px;}
.hrcard .s {font-size:.79rem; opacity:.82; line-height:1.35;}
.hrcard.empty {opacity:.4; border-style:dashed; background:transparent;}
.hrcard.fix {border-color:#d9a406; background:rgba(217,164,6,.10);}
.hrcard.out {border-color:#c96; background:rgba(200,120,80,.10);}
.hrcard.done {border-color:#3a9; background:rgba(50,160,140,.10);}
.hrday {font-weight:700; font-size:.92rem; margin:12px 0 2px; opacity:.85;}
</style>
""", unsafe_allow_html=True)


def card(top: str = "", head: str = "", sub: str = "", tone: str = "") -> str:
    """카드 한 칸 — 위(시간·구분) / 가운데(이름) / 아래(부가 정보)."""
    return (
        f'<div class="hrcard {tone}">'
        f'<div class="t">{escape(str(top))}</div>'
        f'<div class="h">{escape(str(head))}</div>'
        f'<div class="s">{escape(str(sub))}</div></div>'
    )


def card_grid(cards: list[str]) -> None:
    if not cards:
        return
    st.markdown('<div class="hrgrid">' + "".join(cards) + "</div>", unsafe_allow_html=True)


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


def publish_handoff(rid: str, plan_id: str, applicants: list[dict],
                    interviewers: list[dict], by: str) -> dict:
    """HR 가 팀별로 '면접자 명단 + 우리 팀 담당자'를 보낸다 (기존 제출은 남긴다)."""
    doc = load_handoff(rid)
    doc["round_id"] = rid
    doc["plan_id"] = plan_id
    doc["sent_at"] = datetime.now().isoformat(timespec="seconds")
    doc["sent_by"] = by
    teams = doc.setdefault("teams", {})
    names = sorted({(row.get("team") or "미상") for row in applicants}
                   | {(row.get("team") or "미상") for row in interviewers})
    for team in names:
        block = teams.setdefault(team, {})
        block["applicants"] = [
            {
                "applicant_id": row["applicant_id"],
                "name": row.get("name") or row["applicant_id"],
                "degree_type": row.get("degree_type"),
                "major_final": row.get("major_final"),
            }
            for row in applicants if (row.get("team") or "미상") == team
        ]
        block["interviewers"] = [
            {
                "interviewer_id": row["interviewer_id"],
                "name": row.get("name") or row["interviewer_id"],
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


def submit_team(rid: str, team: str, pairs: dict, by: str) -> dict:
    doc = load_handoff(rid)
    block = doc.setdefault("teams", {}).setdefault(team, {})
    block["submitted"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "by": by,
        "pairs": dict(pairs),
    }
    save_handoff(rid, doc)
    return doc


def handoff_pairs(doc: dict) -> dict[str, str]:
    """모든 팀의 제출을 하나로 합친다 (면접자 → 면접 담당자)."""
    out: dict[str, str] = {}
    for block in (doc.get("teams") or {}).values():
        out.update(((block.get("submitted") or {}).get("pairs") or {}))
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
def fetch_rounds():
    """스케줄이 생성된 Round 목록 — 직접 입력하지 않아도 고르게 한다."""
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
    "day": "요일", "hour": "시간", "slot_count": "가능 시간 수",
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


def ko_frame(rows, keep=None, drop_ids: bool = True) -> pd.DataFrame:
    """영어 키를 우리말 컬럼으로 바꾸고 내부 식별자는 걷어낸 표를 만든다."""
    frame = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows or [])
    if frame.empty:
        return frame
    if keep:
        frame = frame[[c for c in keep if c in frame.columns]]
    elif drop_ids:
        frame = frame[[c for c in frame.columns if c not in HIDDEN_COLUMNS]]
    return frame.rename(columns=COLUMN_LABELS)


def role_label(priority) -> str:
    return "팀장" if priority == 1 else "실무"


KIND_LABELS = {"master": "전체 지원자 명단", "team_distribution": "팀별 명단"}
STATUS_LABELS = {
    "DRAFT": "작성 중", "ADJUSTED": "조정됨", "APPROVED": "승인 완료",
    "REJECTED": "반려", "READY": "준비됨", "LOCKED": "확정", "OK": "이상 없음",
    "GENERATED": "생성 완료", "PENDING": "대기", "SENT": "발송 완료",
}


def say(value, table: dict) -> str:
    """코드 값을 우리말로 — 모르는 값은 그대로 보여 준다."""
    text = str(value or "").strip()
    return table.get(text, text or "-")


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
    "우리 팀이 받은 명단을 보고 담당자를 정합니다",
    "서비스 상태와 기록을 봅니다",
]
MENUS = [
    "지원자 명단 받기",
    "팀별 명단 나누기",
    "면접 담당자 정하기",
    "부서에 명단 보내기",
    "면접 시간표 만들기",
]
MENU_HINTS = [
    "받은 엑셀 파일들을 하나로 합쳐 이번 회차의 지원자 명단을 확정합니다.",
    "확정된 명단을 팀별로 나누고 면접 순서를 잡습니다.",
    "이번 회차에 면접을 볼 담당자를 고릅니다.",
    "각 팀에 면접자 명단을 보내고 담당자를 짝지어 줍니다.",
    "전체 면접 시간표를 만들고 확인합니다.",
]

st.session_state.setdefault("round_input", time.strftime("R%Y%m%d-01"))
st.session_state.setdefault("actor", "hr_console")
st.session_state.setdefault("viewer", VIEWERS[0])
st.session_state.setdefault("menu", MENUS[0])


def nav_button(label: str, key: str, active: bool, slot: str, value: str) -> None:
    """라디오 대신 누르는 버튼 — 지금 보고 있는 곳은 진하게 칠한다."""
    if st.button(label, key=key, width="stretch",
                 type="primary" if active else "secondary"):
        st.session_state[slot] = value
        st.rerun()


with st.sidebar:
    st.markdown("## 🎯 면접 진행 도우미")
    viewer = st.session_state["viewer"]
    menu = st.session_state["menu"]

    st.markdown("**어떤 일을 하시나요?**")
    for index, name in enumerate(VIEWERS):
        nav_button(f"{VIEWER_ICONS[index]}  {name}", f"nav_view_{index}",
                   viewer == name, "viewer", name)
    st.caption(VIEWER_HINTS[VIEWERS.index(viewer)] if viewer in VIEWERS else "")

    if viewer == VIEWERS[0]:
        st.divider()
        st.markdown("**진행 순서**")
        for index, name in enumerate(MENUS):
            nav_button(f"{index + 1}. {name}", f"nav_menu_{index}",
                       menu == name, "menu", name)

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


def plan_field(key: str, host=None) -> str:
    """이번 회차의 팀 배정안을 그대로 쓴다 — 화면에 번호를 내걸지 않는다.

    2단계에서 배정안을 만들면 값이 자동으로 따라오므로 고객은 아무것도 입력하지
    않아도 된다. 지난 배정안을 다시 불러야 하는 드문 경우에만 '자세히' 안에서
    번호를 직접 넣는다.
    """
    current = st.session_state.get("plan_id") or ""
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


def reset_round_state() -> None:
    for key in ROUND_STATE_KEYS:
        st.session_state.pop(key, None)


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

    # ---------------- 업로드 · 등록 ----------------
    st.subheader("① 엑셀 파일 올리기")
    st.caption(
        "여기 올린 파일이 이번 회차의 전부가 됩니다. 올리기 → 맞춰 보기 → 확정까지가 "
        "한 묶음이고, 2단계는 그 결과만 가지고 팀별로 나눕니다."
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
                reset_round_state()
                clear_caches()
                st.success(
                    f"파일 {data.get('deleted_versions')}개를 지웠습니다 — "
                    "이번 회차는 비어 있습니다."
                )
                st.rerun()

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
                    # 새로 올렸으니 앞선 대조·배포·시간표 결과는 모두 무효다
                    reset_round_state()
                    st.session_state["v_registered"] = [
                        v["version_id"] for v in data.get("registered", [])
                    ]
                    clear_caches()
                    cleared = data.get("cleared") or {}
                    st.success(
                        f"파일 {data.get('count')}개를 올렸습니다"
                        + (f" · 먼저 올렸던 {cleared.get('deleted_versions')}개는 "
                           "지웠습니다" if cleared.get("deleted_versions") else "")
                    )
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

    if st.button("🔍 맞춰 보기", type="primary", key="v_compare") and picked_ids:
        data, cerr = post_json(
            f"{VERSION_MANAGER}/api/v1/versions/compare", {"version_ids": picked_ids}
        )
        if cerr:
            st.error(cerr)
        else:
            st.session_state["v_compare_result"] = data
            st.session_state["v_selections"] = {}

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
        m1, m2, m3 = st.columns(3)
        m1.metric("지원자", merged.get("applicant_count"))
        m2.metric("줄 수", merged.get("row_count"))
        m3.metric("못 정한 사람", len(merged.get("unresolved") or []))
        st.caption(f"만든 시각 {str(merged.get('created_at'))[:16]}")

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
            st.dataframe(
                pd.DataFrame(preview.get("rows") or []),
                width="stretch", hide_index=True, height=420,
            )


# ============================================================
# 2. 지원자 명단 정리
# ============================================================
NAME_HEADER = "한글성명"
ID_HEADER = "지원자 번호"
DEGREE_HEADER = "최종학력_학교유형"
BACHELOR_CODE = "과정1"

SLOT_MINUTES = 30      # 면접 1건
BREAK_MINUTES = 5      # 면접 사이 휴식
SLOTS_PER_DAY = 8      # 하루 최대 면접 건수


def degree_label(value) -> str:
    """마스터의 학교유형 코드(과정1/2/3)를 학사·대학원으로 읽는다."""
    text = str(value or "").strip()
    if not text:
        return "미상"
    if text in (BACHELOR_CODE, "학사"):
        return "학사"
    return "대학원"


def team_rosters_from_versions(history: list[dict]) -> dict[str, list[dict]]:
    """1번 메뉴에 등록된 희망지원자_{팀} 파일을 팀별 명단으로 읽어 온다."""
    rosters: dict[str, list[dict]] = {}
    for version in history:
        if version.get("kind") != KIND_TEAM or not version.get("is_active"):
            continue
        team = version.get("team_name") or version.get("file_name") or "미상"
        preview, err = fetch_json(
            f"{VERSION_MANAGER}/api/v1/versions/by-id/{version['version_id']}/preview",
            (("limit", 1000),),
        )
        if err or not preview:
            continue
        rosters[team] = [
            {
                "applicant_id": row.get(ID_HEADER, ""),
                "name": row.get(NAME_HEADER, ""),
                "team": team,
                "degree": degree_label(row.get(DEGREE_HEADER)),
            }
            for row in preview.get("rows") or []
            if row.get(NAME_HEADER)
        ]
    return rosters


def team_rosters_from_plan(applicants: list[dict]) -> dict[str, list[dict]]:
    rosters: dict[str, list[dict]] = {}
    for row in applicants:
        rosters.setdefault(row["team"], []).append({
            "applicant_id": row.get("applicant_id", ""),
            "name": row.get("name", ""),
            "team": row["team"],
            "degree": degree_label(row.get("degree_type")),
        })
    return rosters


def slot_labels(start: str, count: int, minutes: int, rest: int) -> list[str]:
    """30분 면접 + 5분 휴식으로 이어지는 시간표 라벨."""
    cursor = datetime.strptime(start, "%H:%M")
    out = []
    for _ in range(count):
        end = cursor + timedelta(minutes=minutes)
        out.append(f"{cursor:%H:%M}~{end:%H:%M}")
        cursor = end + timedelta(minutes=rest)
    return out


def order_for_interview(rows: list[dict], balance: bool) -> list[dict]:
    """가나다순을 기본으로 하되, 학력이 한쪽으로 몰리지 않게 번갈아 배치한다."""
    ordered = sorted(rows, key=lambda r: (r.get("name") or ""))
    if not balance:
        return ordered

    buckets: dict[str, list[dict]] = {}
    for row in ordered:
        buckets.setdefault(row["degree"], []).append(row)
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
    return out


def build_day_table(
    rosters: dict[str, list[dict]], *, start: str, minutes: int, rest: int,
    per_day: int, balance: bool, show_degree: bool,
) -> tuple[pd.DataFrame, int]:
    """팀을 가로, 시간을 세로로 놓고 일자마다 한 줄 띄운 면접 순서표를 만든다."""
    teams = sorted(rosters)
    ordered = {team: order_for_interview(rosters[team], balance) for team in teams}
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
    return pd.DataFrame(rows, columns=["구분"] + teams), days


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
        "팀마다 지원자를 가나다순으로 세우고, [면접 순서 잡기]를 누르면 학사·대학원이 "
        "고르게 섞이도록 차례를 정해 하루 8명씩(30분 면접 + 5분 휴식) 나눕니다."
    )

    sources = {}
    if any(v.get("kind") == KIND_TEAM and v.get("is_active") for v in history):
        sources["1단계에서 올린 팀별 명단"] = "versions"
    if plan_id:
        sources["방금 나눈 팀 배정 결과"] = "plan"
    if not sources:
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
                    "allow_duplicate": True, "duplicate_score_threshold": 0.8,
                    "created_by": actor,
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

    choice = st.radio("어느 명단으로 할까요", list(sources), horizontal=True,
                      key="r_source")
    if sources[choice] == "versions":
        rosters = team_rosters_from_versions(history)
    else:
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
    for team in teams:
        people = sorted(rosters[team], key=lambda r: r.get("name") or "")
        day_title(f"🏢 {team} — {len(people)}명")
        card_grid([
            card(f"{index}번", row.get("name") or row.get("applicant_id"),
                 row.get("degree"))
            for index, row in enumerate(people, start=1)
        ])
    st.caption(
        " · ".join(f"{team} {len(rosters[team])}명" for team in teams)
        + f" · 모두 {sum(len(v) for v in rosters.values())}명 (팀별 가나다순)"
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
    balance = o1.checkbox("학사·대학원 고르게 섞기", value=True, key="r_balance")
    show_degree = o2.checkbox("학력도 같이 보기", value=True, key="r_showdeg")

    if st.button("🗂️ 면접 순서 잡기", type="primary", key="r_organize"):
        try:
            table, days = build_day_table(
                rosters, start=start.strip(), minutes=int(minutes), rest=int(rest),
                per_day=int(per_day), balance=balance, show_degree=show_degree,
            )
        except ValueError:
            st.error("시작 시각은 HH:MM 형식으로 입력하세요. (예: 09:00)")
        else:
            st.session_state["roster_table"] = table
            st.session_state["roster_days"] = days
            st.session_state["roster_matrix"] = matrix

    table = st.session_state.get("roster_table")
    if table is None:
        return

    days = st.session_state.get("roster_days", 0)
    st.success(
        f"{days}일에 걸쳐 · 하루 {int(per_day)}명씩 · 한 명당 {int(minutes)}분 면접 + "
        f"{int(rest)}분 휴식으로 순서를 잡았습니다."
    )
    team_cols = [c for c in table.columns if c != "구분"]
    for _, line in table.iterrows():
        head = str(line["구분"])
        if head.startswith("──"):
            day_title(head)
            continue
        if not head.strip():
            continue
        card_grid([
            card(f"{team} · {head}", str(line[team]).split(" (")[0] or "—",
                 (str(line[team]).split(" (")[1].rstrip(")")
                  if " (" in str(line[team]) else ""),
                 tone="" if str(line[team]).strip() else "empty")
            for team in team_cols
        ])
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

    merged = st.session_state.get("merged_version")
    if merged:
        st.success(
            f"1단계에서 확정한 명단 '{merged.get('file_name')}' "
            f"({merged.get('applicant_count')}명)으로 시작합니다."
        )
    else:
        st.caption("1단계에서 확정 명단을 만들면 그 명단이 아래 기준 명단으로 잡힙니다.")

    st.subheader("① 팀 배정하기")
    label = {
        v["version_id"]: f"{v.get('file_name') or '이름 없는 파일'} · "
                         f"{v.get('applicant_count')}명 · {str(v.get('created_at'))[:16]}"
        for v in masters
    }
    prefer = st.session_state.get("master_version_id")
    ids = list(label)
    index = ids.index(prefer) if prefer in ids else 0

    c1, c2, c3 = st.columns([4, 1, 1])
    master_id = c1.selectbox("기준이 되는 지원자 명단", ids, index=index,
                             format_func=lambda v: label[v], key="d_master")
    allow_dup = c2.checkbox("두 팀이 같이 보기 허용", value=True, key="d_dup",
                            help="한 지원자를 두 팀이 함께 검토할 수 있게 합니다.")
    threshold = c3.number_input("같이 볼 기준 점수", 0.0, 1.0, 0.8, 0.05, key="d_thr")

    if st.button("🧮 팀 배정하기", type="primary", key="d_plan"):
        data, perr = post_json(
            f"{DISTRIBUTOR}/api/v1/distribute/plan",
            {
                "round_id": round_id,
                "master_version_id": master_id,
                "allow_duplicate": allow_dup,
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

    plan_id = plan_field("d_plan_id")
    if plan_id:
        st.session_state["plan_id"] = plan_id
    else:
        plan_id = st.session_state.get("plan_id") or ""

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
        f"{str(summary.get('created_at'))[:16]} · {summary.get('created_by')} 님이 "
        "만든 배정입니다."
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
        st.caption(f"{len(shown)} / {len(adf)}명 보는 중 (같이 보는 인원은 뺀 확정 명단)")
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
                    st.success(
                        f"옮겼습니다 — 지금 상태 {say(data.get('status'), STATUS_LABELS)}"
                    )

    with c2:
        st.markdown("**이대로 확정할까요**")
        if st.button("✅ 이대로 확정", type="primary", key="d_approve"):
            data, aerr2 = post_json(
                f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/approve", {"actor": actor}
            )
            if aerr2:
                st.error(aerr2)
            else:
                clear_caches()
                st.success(f"확정했습니다 — {str(data.get('approved_at'))[:16]}")
        reject_reason = st.text_input("다시 하는 이유", key="d_reject_reason").strip()
        if st.button("⛔ 다시 하기", key="d_reject"):
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
            "소속팀": team,
            "이메일": f"{emp_id.lower()}@example.com",
            "일일최대": 4 if is_leader else 6,
            "우선순위": 1 if is_leader else 2,
        })
    return people


def roster_to_xlsx(people: list[dict]) -> bytes:
    frame = pd.DataFrame(
        people, columns=["사번", "성명", "소속팀", "이메일", "일일최대", "우선순위"]
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
            "소속팀": p["소속팀"], "성명": p["성명"],
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
    st.header("3단계 · 면접 담당자 정하기")
    st.caption(
        "면접에 들어갈 담당자 명단을 등록하고, 이번 회차에 실제로 들어갈 사람만 "
        "골라 둡니다. 여기서 고른 사람에게만 4단계가 명단을 보내고, 5단계가 그 "
        "사람들로만 시간표를 짭니다."
    )
    if not need_round():
        return

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
            "담당자 명단 엑셀 (사번 · 성명 · 소속팀 · 이메일 · 하루 최대 · 역할)",
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
        rows.append({
            "면접 참여": checked,
            "성명": row.get("name") or "",
            "소속팀": row.get("team") or "",
            "역할": role_label(row.get("priority")),
            "이메일": row.get("email") or "",
            "하루 최대": row.get("max_daily"),
            "적어 낸 가능 시간": sum(
                len(v) for v in (row.get("availability") or {}).values()
            ),
            "사번": row["interviewer_id"],
        })

    edited = st.data_editor(
        pd.DataFrame(rows),
        width="stretch", hide_index=True, height=520,
        disabled=["성명", "소속팀", "역할", "이메일", "하루 최대",
                  "적어 낸 가능 시간", "사번"],
        key="i_editor",
    )
    picked = edited[edited["면접 참여"]]["사번"].tolist()
    hidden = sorted(selected_ids - {row["interviewer_id"] for row in visible})

    st.caption(
        f"{len(picked)}명 고름"
        + (f" · 지금 안 보이는 팀에서 이미 고른 {len(hidden)}명도 그대로 남습니다"
           if hidden else "")
    )
    if st.button("💾 이 사람들로 정하기", type="primary", key="i_save"):
        final = list(dict.fromkeys(picked + hidden))
        data, uerr = put_json(
            f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}",
            {"interviewer_ids": final, "actor": actor},
        )
        if uerr:
            st.error(uerr)
        else:
            clear_caches()
            st.success(f"이번 회차 담당자 {data.get('selected')}명으로 정했습니다.")
            st.rerun()

    if selected:
        with st.expander(f"지금 정해 둔 담당자 {len(selected)}명", expanded=False):
            st.dataframe(
                pd.DataFrame([
                    {
                        "소속팀": row.get("team") or "-",
                        "성명": row.get("name") or row["interviewer_id"],
                        "역할": role_label(row.get("priority")),
                        "이메일": row.get("email") or "",
                        "하루 최대": row.get("max_daily"),
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
        "담당자를 골라 보내 주면 그 결과가 아래 짝짓기와 5단계 시간표에 그대로 들어옵니다."
    )
    plan_id = st.session_state.get("plan_id") or ""
    doc = load_handoff(round_id)

    c1, c2 = st.columns([1, 3])
    if c1.button("📤 명단 보내기", type="primary", key="c_publish"):
        if not plan_id:
            st.warning("2단계에서 팀별 명단을 먼저 만들어 주세요.")
        elif not selected:
            st.warning("3단계에서 면접 담당자를 먼저 정해 주세요.")
        else:
            applicants, aerr = plan_applicants(plan_id)
            if aerr:
                st.error(aerr)
            else:
                doc = publish_handoff(round_id, plan_id, applicants, selected, actor)
                st.success(f"{len(doc.get('teams') or {})}개 팀에 명단을 보냈습니다.")
    c2.caption(f"마지막으로 보낸 시각 {str(doc.get('sent_at'))[:16] or '-'}")

    teams = doc.get("teams") or {}
    if not teams:
        st.info("아직 보낸 명단이 없습니다.")
        return
    cards = []
    for team in sorted(teams):
        block = teams[team]
        sub = block.get("submitted") or {}
        pairs = sub.get("pairs") or {}
        cards.append(card(
            f"{team} · 담당자 {len(block.get('interviewers') or [])}명",
            f"면접자 {len(block.get('applicants') or [])}명",
            (f"부서에서 {len(pairs)}명 보내 옴 · {str(sub.get('at'))[:16]}"
             if sub else "부서 회신 기다리는 중"),
            tone="done" if sub else "",
        ))
    card_grid(cards)


def render_matching(selected: list[dict]) -> None:
    """담당자를 고르고 넘어온 명단에서 면접자를 붙인다 (부서 제출과 같은 저장소)."""
    st.subheader("③ 면접 담당자와 면접자 짝짓기")
    st.caption(
        "담당자를 한 명 고른 뒤 명단에서 면접자를 골라 붙여 줍니다. 부서에서 직접 보낸 "
        "내용도 여기 그대로 보입니다. 짝이 없는 사람은 5단계에서 '면접 못 보는 사람'으로 "
        "다시 보여 드립니다."
    )
    doc = load_handoff(round_id)
    teams_doc = doc.get("teams") or {}
    if not teams_doc:
        st.info("위 ②에서 명단을 먼저 보내면 여기서 짝을 지을 수 있습니다.")
        return

    team = st.selectbox("팀", sorted(teams_doc), key="c_match_team")
    block = teams_doc[team]
    team_ap = block.get("applicants") or []
    team_iv = block.get("interviewers") or []
    pairs: dict[str, str] = dict((block.get("submitted") or {}).get("pairs") or {})
    if not team_iv:
        st.warning(f"{team} 에 정해진 면접 담당자가 없습니다 (3단계에서 정해 주세요).")
        return
    if not team_ap:
        st.warning(f"{team} 에 배정된 면접자가 없습니다.")
        return

    load = Counter(pairs.values())
    iv_row = {row["interviewer_id"]: row for row in team_iv}

    def iv_label(iid: str) -> str:
        row = iv_row[iid]
        return (f"{row.get('name') or iid} · {role_label(row.get('priority'))} · "
                f"맡은 사람 {load.get(iid, 0)}/{row.get('max_daily') or '-'}")

    who = st.selectbox("면접 담당자", list(iv_row), format_func=iv_label, key="c_match_iv")
    ap_name = {row["applicant_id"]: row.get("name") or row["applicant_id"]
               for row in team_ap}
    ap_label = {
        row["applicant_id"]: f"{ap_name[row['applicant_id']]} · "
                             f"{degree_label(row.get('degree_type'))} · "
                             f"{row.get('major_final') or ''}"
        for row in team_ap
    }
    mine = [row for row in team_ap if pairs.get(row["applicant_id"]) == who]
    free = [row for row in team_ap if row["applicant_id"] not in pairs]
    picks = st.multiselect(
        f"아직 담당자가 없는 면접자 {len(free)}명 중에서 고르기",
        [row["applicant_id"] for row in free],
        format_func=lambda aid: ap_label.get(aid, aid),
        key=f"c_match_pick_{team}_{who}",
    )

    def commit(new_pairs: dict) -> None:
        submit_team(round_id, team, new_pairs, actor)
        st.rerun()

    b1, b2, b3, b4 = st.columns(4)
    if b1.button(f"➕ {len(picks)}명 맡기기", type="primary", key="c_match_add"):
        commit({**pairs, **{aid: who for aid in picks}})
    if b2.button(f"↩ 이 담당자 짝 풀기 ({len(mine)})", key="c_match_drop"):
        commit({a: i for a, i in pairs.items() if i != who})
    if b3.button("🎯 남은 사람 알아서 나누기", key="c_match_auto"):
        caps = [(row["interviewer_id"], int(row.get("max_daily") or SLOTS_PER_DAY))
                for row in team_iv]
        cursor = 0
        filled = dict(pairs)
        for row in team_ap:
            aid = row["applicant_id"]
            if aid in filled:
                continue
            for _ in range(len(caps)):
                iid, cap = caps[cursor % len(caps)]
                cursor += 1
                if load[iid] < cap:
                    filled[aid] = iid
                    load[iid] += 1
                    break
        commit(filled)
    if b4.button("🧹 이 팀 짝 모두 지우기", key="c_match_reset"):
        commit({})

    st.markdown("**담당자가 맡은 사람**")
    card_grid([
        card(
            f"{role_label(row.get('priority'))} · "
            f"{len([a for a, i in pairs.items() if i == iid])}/{row.get('max_daily') or '-'}",
            row.get("name") or iid,
            ", ".join(sorted(ap_name[a] for a, i in pairs.items()
                             if i == iid and a in ap_name)) or "맡은 사람 없음",
            tone="done" if any(i == iid for i in pairs.values()) else "empty",
        )
        for iid, row in iv_row.items()
    ])

    left = [ap_name[row["applicant_id"]] for row in team_ap
            if row["applicant_id"] not in pairs]
    if left:
        st.markdown("**아직 담당자가 없는 면접자**")
        card_grid([card(team, name, "짝 없음", tone="out") for name in sorted(left)])

    fresh = load_handoff(round_id)
    all_pairs = handoff_pairs(fresh)
    total_ap = sum(len(b.get("applicants") or []) for b in (fresh.get("teams") or {}).values())
    st.caption(
        f"짝이 정해진 사람 {len(all_pairs)} / {total_ap}명 · 아직 없는 사람 "
        f"{total_ap - len(all_pairs)}명 — 짝이 없는 사람은 5단계에서 '면접 못 보는 "
        "사람'으로 다시 보여 드립니다."
    )

    rows = []
    for tname, tblock in sorted((fresh.get("teams") or {}).items()):
        iv_name = {iv["interviewer_id"]: iv.get("name") or iv["interviewer_id"]
                   for iv in (tblock.get("interviewers") or [])}
        tpairs = (tblock.get("submitted") or {}).get("pairs") or {}
        for row in (tblock.get("applicants") or []):
            rows.append({
                "팀": tname,
                "면접자": row.get("name"),
                "지원자 번호": row["applicant_id"],
                "학력": degree_label(row.get("degree_type")),
                "면접 담당자": iv_name.get(tpairs.get(row["applicant_id"]), ""),
            })
    frame = pd.DataFrame(rows)
    with st.expander("표로 보기 · 내려받기"):
        st.dataframe(frame, width="stretch", hide_index=True, height=360)
        st.download_button(
            "⬇ 짝짓기 결과 XLSX", to_excel({"매칭": frame}),
            file_name=f"면접매칭_{round_id}.xlsx", mime=XLSX_MIME, key="c_match_xlsx",
        )


def render_collection() -> None:
    st.header("4단계 · 부서에 명단 보내기")
    st.caption(
        "3단계에서 정한 담당자에게 면접 가능한 시간을 물어보고, 각 팀에 면접자 명단을 "
        "보냅니다. 부서가 보내 준 답이 5단계 시간표의 재료가 됩니다."
    )
    if not need_round():
        return

    selected, serr = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")
    if serr:
        st.error(serr)
        return
    selected = selected or []

    st.subheader("① 이번에 연락할 담당자")
    if selected:
        frame = pd.DataFrame([
            {
                "소속팀": row.get("team") or "미상",
                "성명": row.get("name") or row["interviewer_id"],
                "역할": role_label(row.get("priority")),
                "이메일": row.get("email") or "",
                "하루 최대": row.get("max_daily"),
                "회신": "○" if row.get("availability") else "-",
            }
            for row in selected
        ]).sort_values(["소속팀", "역할", "성명"], ascending=[True, True, True])
        st.dataframe(frame, width="stretch", hide_index=True,
                     height=min(38 * (len(frame) + 1) + 3, 400))
        by_team = frame.groupby("소속팀").size()
        st.caption(
            " · ".join(f"{team} {n}명" for team, n in by_team.items())
            + f" · 모두 {len(frame)}명 (3단계에서 고르지 않은 사람에게는 연락이 "
            "가지 않습니다)"
        )

    st.divider()
    render_send(selected)

    st.divider()
    render_matching(selected)

    st.divider()
    st.subheader("④ 가능한 시간 물어보기")
    if not selected:
        st.warning("3단계에서 이번 회차 면접 담당자를 먼저 정해 주세요.")
    else:
        no_email = [row.get("name") or row["interviewer_id"]
                    for row in selected if not row.get("email")]
        if no_email:
            st.warning(f"이메일이 없어 연락하지 못하는 사람: {', '.join(no_email)}")
        invitees = [
            {
                "name": row.get("name") or row["interviewer_id"],
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
    st.subheader("⑤ 누가 답했는지 보기")
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
    st.subheader("⑥ 모인 가능 시간")
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
                 balance: bool = True) -> list[dict]:
    """매칭(면접자→담당자)만으로 일자별 시간표를 만든다.

    5번 스케줄러가 아직 돌지 않아도 부서가 제출한 즉시 시간표를 볼 수 있어야 한다.
    가나다순을 기본으로 학력을 섞고, 하루 per_day 칸씩 끊어 일차를 매긴다.
    """
    rows = [
        {
            "applicant_id": row["applicant_id"],
            "name": row.get("name") or row["applicant_id"],
            "degree": degree_label(row.get("degree_type")),
            "interviewer_id": pairs.get(row["applicant_id"]),
        }
        for row in applicants if row["applicant_id"] in pairs
    ]
    labels = slot_labels(start, per_day, minutes, rest)
    out = []
    for index, row in enumerate(order_for_interview(rows, balance)):
        out.append({
            **row,
            "day": index // per_day + 1,
            "slot": labels[index % per_day],
            "interviewer": iv_name.get(row["interviewer_id"], row["interviewer_id"] or ""),
        })
    return out


def schedule_cards(rows: list[dict], *, by_person: bool = False) -> None:
    """시간표를 일자(또는 담당자)별 카드로 깐다."""
    if not rows:
        st.info("보여 줄 일정이 없다.")
        return
    if by_person:
        people = sorted({row["interviewer"] for row in rows})
        for person in people:
            mine = sorted([r for r in rows if r["interviewer"] == person],
                          key=lambda r: (r["day"], r["slot"]))
            day_title(f"👤 {person} — {len(mine)}건")
            card_grid([
                card(f"{r['day']}일차 · {r['slot']}", r["name"], r["degree"])
                for r in mine
            ])
        return
    for day in sorted({row["day"] for row in rows}):
        mine = sorted([r for r in rows if r["day"] == day], key=lambda r: r["slot"])
        day_title(f"── {day}일차 ── ({len(mine)}명)")
        card_grid([
            card(r["slot"], r["name"], f"{r['degree']} · 담당 {r['interviewer']}")
            for r in mine
        ])


def render_timetable(assignments: list[dict]) -> None:
    """팀 × 요일 8슬롯 시간표 — 칸마다 '지원자 (면접관)' 을 적는다."""
    st.markdown("### 🗓️ 면접 시간표 (누가 언제 누구를 만나는지)")
    c1, c2, c3, c4 = st.columns(4)
    start = c1.text_input("몇 시부터", value="09:00", key="t_start")
    minutes = c2.number_input("한 명당 면접(분)", 10, 120, SLOT_MINUTES, 5, key="t_min")
    rest = c3.number_input("사이 쉬는 시간(분)", 0, 60, BREAK_MINUTES, 5, key="t_rest")
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
    matched: dict[str, str] = handoff_pairs(load_handoff(round_id))

    # (팀, 요일) 별로 모아 시간대 순으로 줄을 세운 뒤 하루 슬롯 수만큼 끊는다
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in assignments:
        key = (row.get("team") or "미상", row.get("day") or "미정")
        buckets.setdefault(key, []).append(row)
    for items in buckets.values():
        items.sort(key=lambda r: (str(r.get("hour")), r.get("applicant_name") or ""))

    teams = sorted({team for team, _ in buckets})
    days = sorted({day for _, day in buckets},
                  key=lambda d: DAY_ORDER.index(d) if d in DAY_ORDER else 99)
    blank = {team: "" for team in teams}
    rows: list[dict] = []
    overflow: list[dict] = []
    for day_index, day in enumerate(days):
        rows.append({"구분": f"── {day} ──", **blank})
        for slot_index, label in enumerate(labels):
            row = {"구분": label}
            for team in teams:
                items = buckets.get((team, day)) or []
                item = items[slot_index] if slot_index < len(items) else None
                if item is None:
                    row[team] = ""
                else:
                    fixed = matched.get(item.get("applicant_id"))
                    iid = fixed or item.get("interviewer_id")
                    who = names.get(iid, iid)
                    row[team] = (
                        f"{item.get('applicant_name') or item.get('applicant_id')} "
                        f"({who}{'★' if fixed else ''})"
                    )
            rows.append(row)
        for team in teams:
            overflow.extend((buckets.get((team, day)) or [])[int(per_day):])
        if day_index < len(days) - 1:
            rows.append({"구분": "", **blank})  # 요일 사이 빈 줄

    table = pd.DataFrame(rows, columns=["구분"] + teams)
    for day in days:
        day_title(f"── {day} ──")
        for slot_index, label in enumerate(labels):
            cards = []
            for team in teams:
                items = buckets.get((team, day)) or []
                item = items[slot_index] if slot_index < len(items) else None
                if item is None:
                    cards.append(card(team, "—", label, tone="empty"))
                    continue
                fixed = matched.get(item.get("applicant_id"))
                iid = fixed or item.get("interviewer_id")
                cards.append(card(
                    f"{team} · {label}",
                    item.get("applicant_name") or item.get("applicant_id"),
                    f"담당 {names.get(iid, iid)}" + (" ★확정" if fixed else ""),
                    tone="fix" if fixed else "",
                ))
            card_grid(cards)
    with st.expander("표로 보기"):
        st.dataframe(table, width="stretch", hide_index=True,
                     height=min(38 * (len(table) + 1) + 3, 760))
    st.caption(
        f"한 명당 {int(minutes)}분 면접 + {int(rest)}분 휴식 · 하루 {int(per_day)}명 · "
        f"{len(days)}일 · {len(teams)}팀 · 모두 {len(assignments)}건"
        + (f" · ★ 부서가 정해 준 짝 {len(matched)}건을 먼저 반영했습니다" if matched else "")
    )
    if overflow:
        with st.expander(f"⚠ 하루 {int(per_day)}명을 넘겨 못 넣은 {len(overflow)}건"):
            st.dataframe(ko_frame(overflow), width="stretch", hide_index=True)
    st.download_button(
        "⬇ 시간표 XLSX", to_excel({"시간표": table}),
        file_name=f"면접시간표_{round_id}.xlsx", mime=XLSX_MIME, key="t_xlsx",
    )


def render_schedule_body(sc_id: str) -> None:
    """시간표 상세 — 지표 · 분포 · 배정 목록 · 팀별 · 히트맵 · 규칙."""
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
        g1.markdown("**요일별 인원**")
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
        st.markdown("**요일마다 학사·대학원이 고르게 섞였는지**")
        dd = pd.crosstab(df["day"], df["degree"])
        dd = dd.reindex([d for d in DAY_ORDER if d in dd.index])
        st.bar_chart(dd, height=300, stack=False)

    iv_roster, _ = fetch_json(f"{SCHEDULER}/api/v1/interviewers")
    iv_names = {row["interviewer_id"]: row.get("name") or row["interviewer_id"]
                for row in (iv_roster or [])}
    if "interviewer_id" in df:
        st.markdown("**담당자마다 몇 명을 보는지 (한쪽으로 몰리지 않았는지)**")
        by_person = (
            df["interviewer_id"].map(lambda i: iv_names.get(i, i))
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
    pick_days = f2.multiselect("요일", days, default=list(days), key="s_day")
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
    if "interviewer_id" in listed:
        listed["interviewer_name"] = listed["interviewer_id"].map(
            lambda i: iv_names.get(i, i)
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
                    lambda i: iv_names.get(i, i)
                )
            sc = [c for c in ["day", "hour"] if c in tdf.columns]
            if sc:
                tdf = tdf.sort_values(sc)
            st.dataframe(
                ko_frame(tdf, keep=["day", "hour", "applicant_name", "degree",
                                    "interviewer_name", "applicant_id",
                                    "reason_tags"]),
                width="stretch", hide_index=True,
                height=min(38 * (len(tdf) + 1) + 3, 700),
            )

    # ---------------- 히트맵 ----------------
    st.markdown("### 🔥 어느 요일 · 시간에 면접이 몰렸는지")
    hm, herr = fetch_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}/heatmap")
    if herr:
        st.error(herr)
    else:
        hm = hm or {}
        grid, hours, hdays = hm.get("grid", {}), hm.get("hours", []), hm.get("days", [])
        hdf = pd.DataFrame(
            [[grid.get(d, {}).get(h, 0) for h in hours] for d in hdays],
            index=hdays, columns=hours,
        )
        try:  # 색 그라데이션은 matplotlib 이 있을 때만
            st.dataframe(hdf.style.background_gradient(cmap="Blues", axis=None),
                         width="stretch")
        except ImportError:
            st.dataframe(hdf, width="stretch")
        st.caption("칸의 숫자는 그 요일·시간에 잡힌 면접 건수입니다.")

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
    st.subheader("② 면접 못 보는 사람 — 아직 짝이 없는 사람")
    doc = load_handoff(round_id)
    teams_doc = doc.get("teams") or {}
    pairs = handoff_pairs(doc)
    if not teams_doc:
        st.info("4단계에서 명단을 보내고 짝을 지으면 여기에 남는 사람이 보입니다.")
        return

    out_ap, out_iv = [], []
    for team, block in sorted(teams_doc.items()):
        used = set(((block.get("submitted") or {}).get("pairs") or {}).values())
        out_ap += [{**row, "team": team} for row in (block.get("applicants") or [])
                   if row["applicant_id"] not in pairs]
        out_iv += [{**row, "team": team} for row in (block.get("interviewers") or [])
                   if row["interviewer_id"] not in used]
    total_ap = sum(len(b.get("applicants") or []) for b in teams_doc.values())
    total_iv = sum(len(b.get("interviewers") or []) for b in teams_doc.values())

    c1, c2 = st.columns(2)
    c1.metric("면접 못 보는 지원자", f"{len(out_ap)} / {total_ap}")
    c2.metric("맡은 사람 없는 담당자", f"{len(out_iv)} / {total_iv}")

    st.markdown("**면접 못 보는 지원자**")
    if out_ap:
        card_grid([
            card(row["team"], row.get("name") or row["applicant_id"],
                 f"{degree_label(row.get('degree_type'))} · 짝 없음", tone="out")
            for row in sorted(out_ap, key=lambda r: (r["team"], r.get("name") or ""))
        ])
    else:
        st.success("모든 지원자에게 담당자가 정해졌습니다.")

    st.markdown("**맡은 사람이 없는 담당자**")
    if out_iv:
        card_grid([
            card(row["team"], row.get("name") or row["interviewer_id"],
                 role_label(row.get("priority")), tone="out")
            for row in sorted(out_iv, key=lambda r: (r["team"], r.get("name") or ""))
        ])
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
        st.caption("이 사람들은 시간표에 들어가지 않습니다 — 4단계나 부서 화면에서 "
                   "짝을 지어 주면 사라집니다.")


def render_scheduling() -> None:
    st.header("5단계 · 면접 시간표 만들기")
    st.caption(
        "2단계에서 나눈 팀별 명단과 담당자들이 적어 낸 가능한 시간으로 시간표를 만듭니다. "
        "지원자가 자기 팀 사람과 마주치지 않도록 자동으로 피해 줍니다."
    )
    if not need_round():
        return

    st.subheader("① 시간표 만들기")
    c1, c2 = st.columns([4, 1])
    plan_id = plan_field("s_plan", c1)
    c2.write("")
    run = c2.button("▶ 시간표 만들기", type="primary", key="s_generate")
    with st.expander("자세한 설정 (거의 쓸 일 없습니다)"):
        algorithm = st.selectbox("짜는 방식", ["v5", "v4", "v3", "v2", "v1"], key="s_algo",
                                 help="기본값(v5)을 그대로 두시면 됩니다.")

    picked = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")[0] or []
    st.caption(
        f"이번 회차에 정한 면접 담당자 {len(picked)}명"
        + (" — 정해 둔 사람이 없으면 등록된 담당자 전체로 짭니다." if not picked else "")
    )

    if run:
        if not plan_id:
            st.warning("먼저 2단계에서 팀별 명단을 나눠 주세요.")
        else:
            data, err = post_json(
                f"{SCHEDULER}/api/v1/schedules/generate",
                {
                    "round_id": round_id, "plan_id": plan_id,
                    "algorithm": algorithm, "generated_by": actor,
                },
                timeout=180.0,
            )
            if err:
                st.error(err)
            else:
                st.session_state["schedule_id"] = data.get("schedule_id")
                clear_caches()
                st.success("시간표를 만들었습니다. 아래 ③에서 확인하세요.")

    st.divider()
    render_excluded(picked, plan_id)

    st.divider()
    st.subheader("③ 만들어진 시간표 보기")
    rounds = [r for r in fetch_rounds() if r["round_id"] == round_id]
    sc_id = st.session_state.get("schedule_id")
    if rounds:
        labels = [f"{r['at']} 만듦 · 면접자 {r['assigned']}명" for r in rounds]
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

    render_schedule_body(sc_id)


def render_team_view() -> None:
    """부서(면접관) 뷰어 — 받은 명단에서 면접자와 담당자를 골라 제출한다."""
    st.header("우리 팀이 볼 면접 명단")
    st.caption(
        "인사 담당자가 보낸 우리 팀 면접자 명단입니다. 면접 볼 사람을 고르고 "
        "누가 면접을 볼지 정해서 보내 주시면, 팀 시간표와 개인 일정이 바로 만들어집니다."
    )
    if not need_round():
        return

    doc = load_handoff(round_id)
    teams_doc = doc.get("teams") or {}
    if not teams_doc:
        st.info("아직 받은 명단이 없습니다. 인사 담당자가 4단계에서 명단을 보내면 "
                "여기에 나타납니다.")
        return

    team = st.selectbox("우리 팀", sorted(teams_doc), key="tv_team")
    block = teams_doc[team]
    applicants = block.get("applicants") or []
    interviewers = block.get("interviewers") or []
    submitted = block.get("submitted") or {}
    saved: dict[str, str] = dict(submitted.get("pairs") or {})
    iv_name = {row["interviewer_id"]: row.get("name") or row["interviewer_id"]
               for row in interviewers}
    st.caption(
        f"받은 시각 {str(doc.get('sent_at'))[:16]} · 보낸 사람 {doc.get('sent_by')} · "
        f"면접 볼 사람 {len(applicants)}명 · 우리 팀 면접 담당자 {len(interviewers)}명"
        + (f" · 마지막으로 보낸 때 {str(submitted.get('at'))[:16]} ({submitted.get('by')})"
           if submitted else " · 아직 보내기 전")
    )
    if not applicants or not interviewers:
        st.warning("받은 명단이나 담당자가 비어 있습니다. 인사 담당자에게 다시 보내 달라고 "
                   "요청해 주세요.")
        return

    st.divider()
    st.subheader("① 면접 볼 사람 고르고, 담당자 정하기")
    b1, b2, b3 = st.columns([1, 1, 3])
    if b1.button("전체 고르기", key="tv_all"):
        for row in applicants:
            st.session_state[f"tv_on_{team}_{row['applicant_id']}"] = True
        st.rerun()
    if b2.button("전체 지우기", key="tv_none"):
        for row in applicants:
            st.session_state[f"tv_on_{team}_{row['applicant_id']}"] = False
        st.rerun()
    b3.caption("체크한 사람만 보내집니다. 담당자를 따로 고르지 않으면 팀장이 맡습니다.")

    default_iv = next((row["interviewer_id"] for row in interviewers
                       if row.get("priority") == 1), interviewers[0]["interviewer_id"])
    picked: dict[str, str] = {}
    columns = st.columns(4)
    for index, row in enumerate(applicants):
        aid = row["applicant_id"]
        with columns[index % 4].container(border=True):
            st.markdown(f"**{row.get('name') or aid}**")
            st.caption(
                f"{degree_label(row.get('degree_type'))} · {row.get('major_final') or '-'}"
            )
            on = st.checkbox("면접 보기", key=f"tv_on_{team}_{aid}",
                             value=aid in saved)
            who = st.selectbox(
                "면접 담당자", list(iv_name), key=f"tv_iv_{team}_{aid}",
                index=list(iv_name).index(saved.get(aid, default_iv))
                if saved.get(aid, default_iv) in iv_name else 0,
                format_func=lambda i: iv_name[i], label_visibility="collapsed",
            )
            if on:
                picked[aid] = who

    st.divider()
    s1, s2 = st.columns([1, 3])
    if s1.button(f"📮 인사 담당자에게 보내기 ({len(picked)}명)", type="primary",
                 key="tv_submit"):
        submit_team(round_id, team, picked, actor)
        st.session_state["tv_done"] = team
        st.rerun()
    s2.caption("보내면 인사 담당자 화면의 시간표에 바로 반영됩니다.")
    if st.session_state.get("tv_done") == team:
        st.success("보냈습니다 — 아래가 이번에 정한 최종 일정입니다.")

    pairs = dict(saved)
    if not pairs:
        st.info("아직 보낸 내용이 없습니다.")
        return

    st.divider()
    st.subheader("② 우리 팀 면접 시간표")
    c1, c2, c3, c4 = st.columns(4)
    start = c1.text_input("면접 시작 시각", value="09:00", key="tv_start")
    minutes = c2.number_input("한 사람당 면접 시간(분)", 10, 120, SLOT_MINUTES, 5,
                              key="tv_min")
    rest = c3.number_input("사이 쉬는 시간(분)", 0, 60, BREAK_MINUTES, 5, key="tv_rest")
    per_day = c4.number_input("하루에 볼 인원", 1, 20, SLOTS_PER_DAY, key="tv_perday")
    try:
        rows = pair_schedule(
            applicants, pairs, iv_name, start=start.strip(), minutes=int(minutes),
            rest=int(rest), per_day=int(per_day),
        )
    except ValueError:
        st.error("시작 시각은 HH:MM 형식으로 입력하세요. (예: 09:00)")
        return

    schedule_cards(rows)
    st.caption(
        f"{team} · 모두 {len(rows)}명 · 한 사람당 {int(minutes)}분 면접에 "
        f"{int(rest)}분 휴식 · 하루 {int(per_day)}명씩 · {max(r['day'] for r in rows)}일차까지"
    )

    st.subheader("③ 담당자별 면접 일정")
    schedule_cards(rows, by_person=True)

    frame = pd.DataFrame([
        {"일차": r["day"], "시간": r["slot"], "면접자": r["name"],
         "학력": r["degree"], "면접 담당자": r["interviewer"]}
        for r in sorted(rows, key=lambda r: (r["day"], r["slot"]))
    ])
    with st.expander("표로 보기 · 내려받기"):
        st.dataframe(frame, width="stretch", hide_index=True, height=420)
        st.download_button(
            "⬇ 우리 팀 시간표 내려받기", to_excel({team: frame}),
            file_name=f"면접시간표_{team}_{round_id}.xlsx", mime=XLSX_MIME,
            key="tv_xlsx",
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
            data, err = post_json(
                f"{SCHEDULER}/api/v1/schedules/generate",
                {"round_id": sc_round, "plan_id": plan_id, "algorithm": "v5",
                 "generated_by": sc_actor},
                timeout=180.0,
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
if viewer == VIEWERS[1]:
    render_team_view()
elif viewer == VIEWERS[2]:
    render_admin()
elif menu == MENUS[1]:
    render_distribution()
elif menu == MENUS[2]:
    render_interviewers()
elif menu == MENUS[3]:
    render_collection()
elif menu == MENUS[4]:
    render_scheduling()
else:
    render_versions()
