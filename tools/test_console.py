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
import random
import re
import time
from datetime import datetime, timedelta
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
# 사이드바 — 메뉴 · 작업 Round
# ============================================================
MENUS = [
    "1️⃣ 자료취합 버전관리",
    "2️⃣ 지원자 명단 정리",
    "3️⃣ 면접 담당자 선별",
    "4️⃣ 희망자 취합",
    "5️⃣ 면접 일정 분배",
    "⚙️ 관리자",
]

st.session_state.setdefault("round_input", time.strftime("R%Y%m%d-01"))
st.session_state.setdefault("actor", "hr_console")

with st.sidebar:
    st.markdown("## 🎯 HR Interview")
    menu = st.radio("메뉴", MENUS, label_visibility="collapsed")

    st.divider()
    st.markdown("**작업 Round**")
    recent = fetch_round_ids()
    if recent:
        picked = st.selectbox(
            "최근 Round 불러오기", ["(직접 입력)"] + recent, key="round_pick",
            label_visibility="collapsed",
        )
        # 셀렉트박스로 고른 값을 아래 text_input 의 기본값으로 밀어 넣는다.
        # (위젯 생성 전에 세션 값을 바꿔야 Streamlit 이 예외를 내지 않는다)
        if picked != "(직접 입력)" and picked != st.session_state.get("_round_pick_last"):
            st.session_state["_round_pick_last"] = picked
            st.session_state["round_input"] = picked
    round_id = st.text_input("Round ID", key="round_input").strip()
    st.session_state["round_id"] = round_id
    actor = st.text_input("담당자", key="actor").strip() or "hr_console"

    st.caption(
        f"plan `{st.session_state.get('plan_id') or '-'}`\n\n"
        f"schedule `{st.session_state.get('schedule_id') or '-'}`"
    )
    if st.button("🔄 캐시 비우기", width="stretch"):
        clear_caches()
        st.rerun()


def need_round() -> bool:
    if not round_id:
        st.warning("좌측에서 작업 Round ID 를 먼저 입력하세요.")
        return False
    return True


# ============================================================
# 1. 자료취합 버전관리
# ============================================================
_TEAM_FILE = re.compile(r"희망지원자[_\-\s]*(?P<team>.+)$")
KIND_MASTER = "master"
KIND_TEAM = "team_distribution"


def classify_local(file_name: str) -> tuple[str, str]:
    """서버(01 merge_service.classify_file)와 같은 규칙 — 업로드 직후 미리 보여준다."""
    stem = Path(file_name or "").stem.strip()
    match = _TEAM_FILE.search(stem)
    if match:
        return KIND_TEAM, (match.group("team").strip(" _-") or "미상")
    return KIND_MASTER, ""


def render_versions() -> None:
    st.header("1️⃣ 자료취합 버전관리")
    st.caption(
        "여러 취합본을 한꺼번에 올려 값이 어긋난 지원자를 집어내고, 행마다 채택할 "
        "버전을 골라 최종 취합본을 만든다. 배포본(희망지원자_{팀}.xlsx)을 같이 올리면 "
        "중복 배포·미배포까지 함께 검사한다."
    )
    if not need_round():
        return

    # ---------------- 업로드 · 등록 ----------------
    st.subheader("① 파일 등록")
    uploads = st.file_uploader(
        "취합본 / 팀 배포본 (여러 개 선택 가능)", type=["xlsx"],
        accept_multiple_files=True, key="v_uploads",
    )
    if uploads:
        kinds: list[str] = []
        st.markdown("**자동 판별 결과** — 틀리면 여기서 고친 뒤 등록한다")
        for index, upload in enumerate(uploads):
            auto_kind, auto_team = classify_local(upload.name)
            c1, c2, c3 = st.columns([4, 2, 2])
            c1.write(f"`{upload.name}` ({len(upload.getvalue()) / 1024:.0f} KB)")
            kind = c2.selectbox(
                "종류", [KIND_MASTER, KIND_TEAM],
                index=0 if auto_kind == KIND_MASTER else 1,
                key=f"v_kind_{index}", label_visibility="collapsed",
            )
            team = c3.text_input(
                "팀", value=auto_team, key=f"v_team_{index}",
                placeholder="배포본이면 팀 이름", label_visibility="collapsed",
            ).strip()
            if kind == KIND_TEAM and not team:
                team = "미상"
            kinds.append(f"{kind}:{team}" if kind == KIND_TEAM else kind)

        if st.button("📥 등록", type="primary", key="v_register"):
            files = [
                ("files", (u.name, u.getvalue(), XLSX_MIME)) for u in uploads
            ]
            try:
                r = http().post(
                    f"{VERSION_MANAGER}/api/v1/versions/register-batch",
                    files=files,
                    data={"round_id": round_id, "actor": actor, "kinds": ",".join(kinds)},
                    timeout=120.0,
                )
            except Exception as exc:
                st.error(str(exc))
            else:
                if r.status_code >= 300:
                    st.error(error_text(r))
                else:
                    data = unwrap(r) or {}
                    st.session_state["v_registered"] = [
                        v["version_id"] for v in data.get("registered", [])
                    ]
                    clear_caches()
                    st.success(f"{data.get('count')}개 등록 완료")
                    st.dataframe(
                        pd.DataFrame(data.get("registered", [])),
                        width="stretch", hide_index=True,
                    )
    elif DEFAULT_MASTER.exists():
        st.caption(f"참고: 기본 마스터 파일이 `{DEFAULT_MASTER.name}` 에 있다.")

    # ---------------- 등록 이력 · 대조 대상 선택 ----------------
    # Round 가 바뀌면 이전 회차의 선택·대조 결과가 남아 있으면 안 된다
    if st.session_state.get("v_round") != round_id:
        st.session_state["v_round"] = round_id
        for key in ("v_compare_pick", "v_compare_result", "v_selections", "v_registered",
                    "v_auto_key", "v_auto_done", "merged_version"):
            st.session_state.pop(key, None)

    st.divider()
    st.subheader("② 대조할 버전 선택")
    history, err = fetch_json(f"{VERSION_MANAGER}/api/v1/versions/{round_id}/history")
    if err:
        st.error(err)
        return
    history = history or []
    if not history:
        st.info("이 Round 에 등록된 파일이 없다. 위에서 먼저 등록하세요.")
        return

    st.dataframe(
        pd.DataFrame(history)[
            ["version_id", "kind", "team_name", "file_name", "applicant_count",
             "actor", "is_active", "created_at"]
            if "file_name" in history[0] else list(history[0].keys())
        ],
        width="stretch", hide_index=True, height=min(38 * (len(history) + 1) + 3, 320),
    )

    label = {
        v["version_id"]: f"[{v['kind']}] {v.get('file_name') or v['version_id'][:8]}"
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
        "대조 대상", list(label), default=default_ids,
        format_func=lambda vid: label[vid], key="v_compare_pick",
    )

    if st.button("🔍 대조 실행", type="primary", key="v_compare") and picked_ids:
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
    st.subheader("③ 대조 결과")
    names = {v["version_id"]: v.get("file_name") or v["version_id"][:8]
             for v in result.get("versions", [])}

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("마스터", len(result.get("master_version_ids", [])))
    m2.metric("배포본", len(result.get("team_version_ids", [])))
    m3.metric("값 불일치", result.get("conflict_count", 0))
    m4.metric("완전 일치", result.get("identical_count", 0))

    for bad in result.get("unreadable", []):
        st.error(f"읽지 못한 파일: {bad.get('reason')}")

    only_in = {vid: ids for vid, ids in (result.get("only_in") or {}).items() if ids}
    if only_in:
        with st.expander(f"한쪽에만 있는 지원자 ({sum(len(v) for v in only_in.values())}명)"):
            for vid, ids in only_in.items():
                st.markdown(f"**{names.get(vid, vid)}** — {len(ids)}명")
                st.code(", ".join(ids[:200]) + (" …" if len(ids) > 200 else ""))

    integrity = result.get("integrity")
    if integrity:
        st.markdown("#### 🧾 배포 무결성 (마스터 ↔ 팀 배포본)")
        status = integrity.get("status")
        (st.success if status == "OK" else st.warning)(f"status: {status}")
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("마스터 인원", integrity.get("master_count"))
        i2.metric("배포된 인원", integrity.get("distributed_count"))
        i3.metric("미배포", integrity.get("undistributed_count"))
        i4.metric("중복 배포", integrity.get("duplicate_count"))
        issues = integrity.get("issues") or []
        if issues:
            rows = []
            for issue in issues:
                if issue["type"] == "UNDISTRIBUTED":
                    rows.append({
                        "유형": "미배포", "지원자": f"{issue.get('count')}명",
                        "상세": ", ".join((issue.get("applicant_ids") or [])[:50]),
                    })
                else:
                    rows.append({
                        "유형": issue["type"], "지원자": issue.get("applicant_id"),
                        "상세": ", ".join(issue.get("teams") or []),
                    })
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    conflicts = result.get("conflicts") or []
    masters = [vid for vid in result.get("master_version_ids", []) if vid in names]
    selections: dict[str, str] = st.session_state.setdefault("v_selections", {})

    st.markdown("#### ⚖️ 값이 어긋난 지원자 — 채택할 버전 선택")
    if not conflicts:
        st.success("마스터끼리 어긋난 값이 없다 — 고를 것이 없으므로 그대로 최종 취합본이 된다.")
        selections.clear()
    else:
        b1, b2 = st.columns([3, 1])
        bulk = b1.selectbox(
            "일괄 채택 기준", masters, format_func=lambda v: names.get(v, v),
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
                st.markdown(f"**{aid}** · {conflict.get('name') or '(이름 없음)'}")
                diff_rows = []
                for field in conflict["fields"]:
                    row = {"컬럼": field["column"]}
                    for vid, value in field["values"].items():
                        row[names.get(vid, vid)] = value
                    diff_rows.append(row)
                st.dataframe(pd.DataFrame(diff_rows), width="stretch", hide_index=True)

                present = conflict["present_in"]
                current = selections.get(aid, present[0])
                index = present.index(current) if current in present else 0
                choice = st.radio(
                    "채택", present, index=index, horizontal=True,
                    format_func=lambda v: names.get(v, v), key=f"v_sel_{aid}",
                )
                selections[aid] = choice

        undecided = [c["applicant_id"] for c in conflicts if c["applicant_id"] not in selections]
        st.caption(
            f"선택 완료 {len(conflicts) - len(undecided)} / {len(conflicts)}건 "
            "— 고르지 않은 행은 기준 파일 값을 그대로 쓴다."
        )

    # ---------------- 최종 파일 생성 ----------------
    st.divider()
    st.subheader("④ 최종 취합본 생성")
    if not masters:
        in_round = [v for v in history if v.get("kind") == KIND_MASTER]
        if in_round:
            st.warning(
                "대조 대상에 마스터(전체 취합본)가 빠져 있어 최종본을 만들 수 없다. "
                "위 ②에서 아래 파일을 선택해 다시 대조하세요 — "
                + ", ".join(v.get("file_name") or v["version_id"][:8] for v in in_round)
            )
            if st.button("🔁 마스터를 넣어 다시 대조", key="v_add_master"):
                st.session_state["v_registered"] = (
                    picked_ids + [v["version_id"] for v in in_round]
                )
                st.session_state.pop("v_compare_pick", None)
                st.session_state.pop("v_compare_result", None)
                st.rerun()
        else:
            st.warning(
                "이 회차에는 배포본(희망지원자_{팀}.xlsx)만 있고 전체 취합본(마스터)이 없다. "
                "①에서 마스터 파일을 올리세요. 파일명이 '희망지원자_' 로 시작하면 팀 배포본으로 "
                "분류되므로, 마스터는 '취합파일.xlsx' 처럼 다른 이름이어야 한다. "
                "이름이 그런데도 배포본으로 잡혔다면 ① 등록 화면에서 종류를 master 로 고쳐 "
                "다시 등록하면 된다."
            )
        return

    c1, c2 = st.columns([2, 3])
    base = c1.selectbox("기준 파일", masters, format_func=lambda v: names.get(v, v),
                        key="v_base")
    out_name = c2.text_input("생성할 파일명", value=f"취합_최종_{round_id}.xlsx",
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
        st.info("어긋난 값이 없어 대조 결과를 그대로 최종 취합본으로 확정했다.")

    if st.button("🧬 최종 파일 생성", type="primary", key="v_merge"):
        err_merge = make_final(base, out_name)
        if err_merge:
            st.error(err_merge)
        else:
            st.session_state["v_auto_done"] = False

    merged = st.session_state.get("merged_version")
    if merged:
        st.success(f"생성 완료: {merged.get('file_name')}")
        m1, m2, m3 = st.columns(3)
        m1.metric("지원자", merged.get("applicant_count"))
        m2.metric("행 수", merged.get("row_count"))
        m3.metric("미해결", len(merged.get("unresolved") or []))
        st.caption(
            f"version_id `{merged.get('version_id')}` · fingerprint "
            f"`{str(merged.get('fingerprint'))[:16]}…` · 채택 출처 {merged.get('rows_from')}"
        )

        vid = merged["version_id"]
        blob, berr = fetch_bytes(f"{VERSION_MANAGER}/api/v1/versions/by-id/{vid}/file")
        if berr:
            st.error(berr)
        else:
            st.download_button(
                "⬇ 최종 파일 다운로드", blob, file_name=merged.get("file_name"),
                mime=XLSX_MIME, key="v_download",
            )

        preview, perr = fetch_json(
            f"{VERSION_MANAGER}/api/v1/versions/by-id/{vid}/preview", (("limit", 100),)
        )
        if perr:
            st.error(perr)
        elif preview:
            st.markdown(f"**미리보기** — 전체 {preview.get('total_rows')}행 중 앞부분")
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


def render_roster_organizer(history: list[dict], plan_id: str) -> None:
    """팀별 명단(가로=팀 · 세로=가나다순) + 일자별 면접 순서표."""
    st.subheader("② 팀별 명단 정리")
    st.caption(
        "가로는 팀, 세로는 가나다순 지원자다. [명단 정리]를 누르면 학력이 고르게 "
        "섞이도록 순서를 잡고 하루 최대 8건(30분 면접 + 5분 휴식)으로 일자를 나눈다."
    )

    sources = {}
    if any(v.get("kind") == KIND_TEAM and v.get("is_active") for v in history):
        sources["팀 배포본 (1번에서 올린 희망지원자 파일)"] = "versions"
    if plan_id:
        sources["배포안 확정 명단 (2번에서 만든 배포안)"] = "plan"
    if not sources:
        st.info(
            "정리할 명단이 없다. 1번 메뉴에서 희망지원자_{팀}.xlsx 를 올리거나, "
            "위에서 배포안을 먼저 만드세요."
        )
        return

    choice = st.radio("명단 출처", list(sources), horizontal=True, key="r_source")
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
        st.warning("읽어 온 명단이 비어 있다.")
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
    st.dataframe(matrix, width="stretch", height=min(38 * (longest + 1) + 3, 520))
    st.caption(
        " · ".join(f"{team} {len(rosters[team])}명" for team in teams)
        + f" · 합계 {sum(len(v) for v in rosters.values())}명"
    )

    st.markdown("**정리 조건**")
    c1, c2, c3, c4 = st.columns(4)
    start = c1.text_input("시작 시각", value="09:00", key="r_start")
    minutes = c2.number_input("면접(분)", 10, 120, SLOT_MINUTES, 5, key="r_min")
    rest = c3.number_input("휴식(분)", 0, 60, BREAK_MINUTES, 5, key="r_rest")
    per_day = c4.number_input("하루 최대", 1, 20, SLOTS_PER_DAY, key="r_perday")
    o1, o2 = st.columns(2)
    balance = o1.checkbox("학력 고르게 섞기", value=True, key="r_balance")
    show_degree = o2.checkbox("학력 함께 표시", value=True, key="r_showdeg")

    if st.button("🗂️ 명단 정리", type="primary", key="r_organize"):
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
        f"{days}일차까지 · 하루 {int(per_day)}건 · {int(minutes)}분 면접 + "
        f"{int(rest)}분 휴식 기준으로 정리했다."
    )
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
    st.header("2️⃣ 지원자 명단 정리")
    st.caption(
        "docs/HR_후보자_팀배포_업무프로세스.md 의 STEP 1~10 을 그대로 수행한다. "
        "서류합격·R&D·조직 필터 → 주력 직무·전공 매칭 → 학사/대학원 비율 → 특수 태그 "
        "→ 중복 검토 → 사유 태그가 붙은 팀별 배포안."
    )
    if not need_round():
        return

    history, err = fetch_json(f"{VERSION_MANAGER}/api/v1/versions/{round_id}/history")
    if err:
        st.error(err)
        return
    masters = [v for v in (history or []) if v.get("kind") == KIND_MASTER]
    if not masters:
        st.warning("이 Round 에 마스터 버전이 없다. 1번 메뉴에서 먼저 등록/병합하세요.")
        return

    st.subheader("① 배포안 생성")
    label = {
        v["version_id"]: f"{v.get('file_name') or v['version_id'][:8]} · "
                         f"{v.get('applicant_count')}명 · {str(v.get('created_at'))[:16]}"
        for v in masters
    }
    prefer = st.session_state.get("master_version_id")
    ids = list(label)
    index = ids.index(prefer) if prefer in ids else 0

    c1, c2, c3 = st.columns([4, 1, 1])
    master_id = c1.selectbox("마스터 버전", ids, index=index,
                             format_func=lambda v: label[v], key="d_master")
    allow_dup = c2.checkbox("복수 검토 허용", value=True, key="d_dup")
    threshold = c3.number_input("중복 임계", 0.0, 1.0, 0.8, 0.05, key="d_thr")

    if st.button("🧮 배포안 생성", type="primary", key="d_plan"):
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

    plan_id = st.text_input(
        "Plan ID", value=st.session_state.get("plan_id") or "", key="d_plan_id"
    ).strip()
    if plan_id:
        st.session_state["plan_id"] = plan_id

    st.divider()
    render_roster_organizer(history or [], plan_id)
    if not plan_id:
        return

    summary, serr = fetch_json(f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}")
    if serr:
        st.error(serr)
        return
    summary = summary or {}

    st.divider()
    st.subheader("③ 배포안 검수")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("배정 인원", summary.get("total_applicants"))
    s2.metric("중복 배포", summary.get("duplicate_count"))
    s3.metric("필터 탈락", summary.get("filtered_count"))
    s4.metric("상태", summary.get("status"))
    st.caption(
        f"plan `{plan_id}` · master `{summary.get('master_version_id')}` · "
        f"생성 {str(summary.get('created_at'))[:19]} by {summary.get('created_by')}"
    )

    team_counts = summary.get("team_counts") or {}
    if team_counts:
        counts = pd.Series(team_counts, name="배정").sort_index().to_frame()
        profiles, _ = fetch_json(f"{DISTRIBUTOR}/api/v1/profiles")
        if profiles:
            target = {p["team_name"]: p.get("target_headcount") for p in profiles}
            counts["정원"] = [target.get(team) for team in counts.index]
        st.dataframe(counts, width="stretch")
        st.bar_chart(counts, height=280)

    unassigned = summary.get("unassigned") or []
    if unassigned:
        with st.expander(f"어느 팀에도 못 간 지원자 {len(unassigned)}명"):
            st.code(", ".join(unassigned[:300]))

    applicants, aerr = fetch_json(f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/applicants")
    if aerr:
        st.error(aerr)
    elif applicants:
        adf = pd.DataFrame(applicants)
        teams = sorted(adf["team"].dropna().unique()) if "team" in adf else []
        pick_teams = st.multiselect("팀 필터", teams, default=list(teams), key="d_team_f")
        shown = adf[adf["team"].isin(pick_teams)] if teams else adf
        st.dataframe(shown, width="stretch", hide_index=True, height=460)
        st.caption(f"{len(shown)} / {len(adf)}명 표시 (중복 배정 제외한 확정 명단)")
        st.download_button(
            "⬇ 확정 명단 CSV", shown.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"applicants_{plan_id[:8]}.csv", mime="text/csv", key="d_csv",
        )

        if teams:
            e1, e2 = st.columns([3, 2])
            export_team = e1.selectbox("팀별 엑셀 내보내기", teams, key="d_export")
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
    st.subheader("④ 조정 · 확정")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**수동 조정** (STEP 10 — HR 담당자 검수)")
        a1, a2, a3 = st.columns(3)
        move_id = a1.text_input("지원자 번호", key="d_mv_id").strip()
        move_from = a2.text_input("현재 팀", key="d_mv_from").strip()
        move_to = a3.text_input("옮길 팀", key="d_mv_to").strip()
        reason = st.text_input("사유", key="d_mv_reason").strip()
        if st.button("↔ 이동 적용", key="d_adjust"):
            if not (move_id and move_from and move_to):
                st.warning("지원자 번호 · 현재 팀 · 옮길 팀을 모두 채우세요.")
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
                    st.success(f"조정 완료 — 상태 {data.get('status')}")

    with c2:
        st.markdown("**승인 / 반려**")
        if st.button("✅ 승인", type="primary", key="d_approve"):
            data, aerr2 = post_json(
                f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/approve", {"actor": actor}
            )
            if aerr2:
                st.error(aerr2)
            else:
                clear_caches()
                st.success(f"승인 완료 — {data.get('approved_at')}")
        reject_reason = st.text_input("반려 사유", key="d_reject_reason").strip()
        if st.button("⛔ 반려", key="d_reject"):
            if not reject_reason:
                st.warning("반려 사유를 입력하세요.")
            else:
                data, rerr = post_json(
                    f"{DISTRIBUTOR}/api/v1/distribute/{plan_id}/reject",
                    {"reason": reject_reason},
                )
                if rerr:
                    st.error(rerr)
                else:
                    clear_caches()
                    st.warning(f"반려 처리됨 — 상태 {data.get('status')}")

    with st.expander("팀 프로필 (배포 기준값 — STEP 4·5·6·7)"):
        profiles, perr2 = fetch_json(f"{DISTRIBUTOR}/api/v1/profiles")
        if perr2:
            st.error(perr2)
        elif profiles:
            st.dataframe(pd.DataFrame(profiles), width="stretch", hide_index=True)
            names = [p["team_name"] for p in profiles]
            target = st.selectbox("수정할 팀", names, key="d_prof_team")
            current = next(p for p in profiles if p["team_name"] == target)
            f1, f2 = st.columns(2)
            primary = f1.text_input("주력 직무 (쉼표)", ", ".join(current["primary_job"]),
                                    key="d_p_primary")
            secondary = f2.text_input("보조 직무 (쉼표)", ", ".join(current["secondary_job"]),
                                      key="d_p_secondary")
            majors = st.text_input("선호 전공 (쉼표)", ", ".join(current["preferred_majors"]),
                                   key="d_p_majors")
            orgs = st.text_input("허용 조직 (쉼표)", ", ".join(current["org_allowed"]),
                                 key="d_p_orgs")
            g1, g2, g3 = st.columns(3)
            ratio = g1.number_input("대학원 비율", 0.0, 1.0,
                                    float(current["grad_ratio_target"]), 0.05,
                                    key="d_p_ratio")
            headcount = g2.number_input("정원", 0, 999, int(current["target_headcount"]),
                                        key="d_p_head")
            tags = g3.text_input("특수 태그 (쉼표)", ", ".join(current["special_tags"]),
                                 key="d_p_tags")

            def split(text: str) -> list[str]:
                return [t.strip() for t in text.split(",") if t.strip()]

            if st.button("💾 프로필 저장", key="d_prof_save"):
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
                    st.success(f"{target} 프로필 저장 — 다시 배포안을 만들면 반영된다.")


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
        st.info("팀 정보를 읽지 못했다. 2번 메뉴에서 배포안을 먼저 만들거나 명단을 올리세요.")
        return

    c1, c2 = st.columns([3, 1])
    pick = c1.multiselect("생성할 팀", teams, default=teams, key="i_gen_teams")
    per_team = c2.number_input("팀당 인원", 1, 20, PER_TEAM_DEFAULT, key="i_gen_n")

    people = [
        person
        for team_no, team in enumerate(pick, start=1)
        for person in make_team_interviewers(team, team_no, int(per_team))
    ]
    if not people:
        return
    st.dataframe(pd.DataFrame(people), width="stretch", hide_index=True,
                 height=min(38 * (len(people) + 1) + 3, 320))

    g1, g2 = st.columns([1, 2])
    auto_select = g2.checkbox("생성과 동시에 이번 회차 투입 인원으로 선별", value=True,
                              key="i_gen_auto")
    if not g1.button(f"👥 {len(people)}명 생성", type="primary", key="i_gen_go"):
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
    message = (f"{data.get('parsed')}명 생성/반영 (신규 {data.get('created')} · "
               f"갱신 {data.get('updated')})")
    if auto_select:
        picked, uerr = put_json(
            f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}",
            {"interviewer_ids": [p["사번"] for p in people], "actor": actor},
        )
        if uerr:
            st.error(uerr)
        else:
            clear_caches()
            message += f" · 이번 회차 {picked.get('selected')}명 선별"
    st.success(message)
    st.rerun()


def render_interviewers() -> None:
    st.header("3️⃣ 면접 담당자 선별")
    st.caption(
        "면접관명단(사번 · 성명 · 소속팀 · 이메일 · 일일최대 · 우선순위)을 올려 마스터에 "
        "반영하고, 이번 회차에 투입할 사람만 골라 둔다. 여기서 고른 사람에게만 4번 메뉴가 "
        "일정 요청을 보내고, 5번 메뉴가 그 사람들로만 시간표를 짠다."
    )
    if not need_round():
        return

    st.subheader("① 명단 준비")
    tab_gen, tab_up = st.tabs(["👥 팀별 자동 생성", "📄 엑셀 업로드"])

    with tab_gen:
        st.caption(
            "실제 명단이 아직 없을 때, 팀마다 6명씩 가상 면접관을 만들어 둔다. "
            "팀 이름으로 시드를 고정하므로 다시 눌러도 같은 사람이 나온다."
        )
        render_interviewer_generator()

    with tab_up:
        if INTERVIEWER_SAMPLE.exists():
            st.download_button(
                "⬇ 샘플 양식 받기", INTERVIEWER_SAMPLE.read_bytes(),
                file_name=INTERVIEWER_SAMPLE.name, mime=XLSX_MIME, key="i_sample",
            )
        upload = st.file_uploader("면접관명단.xlsx", type=["xlsx"], key="i_upload")
        if upload is not None and st.button("📥 업로드", type="primary", key="i_import"):
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
                        f"{data.get('parsed')}명 반영 (신규 {data.get('created')} · "
                        f"갱신 {data.get('updated')}) · 팀 "
                        f"{', '.join(data.get('teams') or [])}"
                    )

    st.divider()
    st.subheader("② 이번 회차 투입 인원")
    roster, rerr = fetch_json(f"{SCHEDULER}/api/v1/interviewers")
    if rerr:
        st.error(rerr)
        return
    roster = roster or []
    if not roster:
        st.info("등록된 면접관이 없다. 위에서 명단을 올리세요.")
        return

    selected, serr = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")
    if serr:
        st.error(serr)
        return
    selected_ids = {row["interviewer_id"] for row in (selected or [])}

    teams = sorted({row["team"] for row in roster if row.get("team")})
    f1, f2 = st.columns([3, 2])
    pick_teams = f1.multiselect("팀 필터", teams, default=list(teams), key="i_team_f")
    preset = f2.selectbox(
        "일괄 선택", ["(변경 없음)", "표시된 전체 선택", "표시된 전체 해제",
                  "팀장(우선순위 1)만 선택"], key="i_preset",
    )

    visible = [row for row in roster if row.get("team") in pick_teams]
    rows = []
    for row in visible:
        checked = row["interviewer_id"] in selected_ids
        if preset == "표시된 전체 선택":
            checked = True
        elif preset == "표시된 전체 해제":
            checked = False
        elif preset == "팀장(우선순위 1)만 선택":
            checked = row.get("priority") == 1
        rows.append({
            "선택": checked,
            "사번": row["interviewer_id"],
            "성명": row.get("name") or "",
            "소속팀": row.get("team") or "",
            "이메일": row.get("email") or "",
            "일일최대": row.get("max_daily"),
            "우선순위": row.get("priority"),
            "가용슬롯": sum(len(v) for v in (row.get("availability") or {}).values()),
        })

    edited = st.data_editor(
        pd.DataFrame(rows),
        width="stretch", hide_index=True, height=520,
        disabled=["사번", "성명", "소속팀", "이메일", "일일최대", "우선순위", "가용슬롯"],
        key="i_editor",
    )
    picked = edited[edited["선택"]]["사번"].tolist()
    hidden = sorted(selected_ids - {row["interviewer_id"] for row in visible})

    st.caption(
        f"선택 {len(picked)}명" + (f" · 필터 밖 기존 선택 {len(hidden)}명은 유지된다" if hidden else "")
    )
    if st.button("💾 회차 선별 저장", type="primary", key="i_save"):
        final = list(dict.fromkeys(picked + hidden))
        data, uerr = put_json(
            f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}",
            {"interviewer_ids": final, "actor": actor},
        )
        if uerr:
            st.error(uerr)
        else:
            clear_caches()
            st.success(f"{data.get('selected')}명 선별 저장 완료")
            st.rerun()

    if selected:
        with st.expander(f"현재 저장된 선별 인원 {len(selected)}명", expanded=False):
            st.dataframe(pd.DataFrame(selected), width="stretch", hide_index=True)


# ============================================================
# 4. 희망자 취합
# ============================================================
def render_collection() -> None:
    st.header("4️⃣ 희망자 취합")
    st.caption(
        "선별된 면접관에게 가능 시간 요청을 보내고, 회신을 모아 팀별 가용 슬롯으로 "
        "집계한다. 여기서 모인 가용성이 5번 메뉴의 시간표 입력이 된다."
    )
    if not need_round():
        return

    selected, serr = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")
    if serr:
        st.error(serr)
        return
    selected = selected or []

    st.subheader("① 대상자 — 3번에서 선별된 사람만")
    if selected:
        frame = pd.DataFrame([
            {
                "소속팀": row.get("team") or "미상",
                "성명": row.get("name") or row["interviewer_id"],
                "사번": row["interviewer_id"],
                "이메일": row.get("email") or "",
                "역할": "팀장" if row.get("priority") == 1 else "실무",
                "일일최대": row.get("max_daily"),
                "회신": "○" if row.get("availability") else "-",
            }
            for row in selected
        ]).sort_values(["소속팀", "역할", "성명"], ascending=[True, True, True])
        st.dataframe(frame, width="stretch", hide_index=True,
                     height=min(38 * (len(frame) + 1) + 3, 400))
        by_team = frame.groupby("소속팀").size()
        st.caption(
            " · ".join(f"{team} {n}명" for team, n in by_team.items())
            + f" · 합계 {len(frame)}명 (선별되지 않은 사람에게는 요청이 가지 않는다)"
        )

    st.divider()
    st.subheader("② 요청 발송")
    if not selected:
        st.warning("3번 메뉴에서 이번 회차 면접관을 먼저 선별하세요.")
    else:
        no_email = [row["interviewer_id"] for row in selected if not row.get("email")]
        if no_email:
            st.warning(f"이메일이 없어 제외되는 면접관: {', '.join(no_email)}")
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
            "회신 마감일", value=(datetime.now() + timedelta(days=3)).date(), key="c_date"
        )
        deadline_time = c2.time_input(
            "마감 시각", value=datetime.strptime("18:00", "%H:%M").time(), key="c_time"
        )
        plan_id = c3.text_input(
            "Plan ID", value=st.session_state.get("plan_id") or "", key="c_plan"
        ).strip()

        st.dataframe(pd.DataFrame(invitees), width="stretch", hide_index=True, height=260)
        if st.button(f"📨 {len(invitees)}명에게 요청 발송", type="primary", key="c_send"):
            if not plan_id:
                st.warning("Plan ID 가 필요하다 (2번 메뉴에서 배포안을 먼저 만드세요).")
            elif not invitees:
                st.warning("보낼 대상이 없다.")
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
                    st.success(
                        f"요청 발송 완료 — request `{data.get('request_id')}` · "
                        f"{data.get('sent_count')}건"
                    )

    st.divider()
    st.subheader("③ 회신 현황")
    responses, rerr = fetch_json(f"{COLLECTOR}/api/v1/responses/{round_id}")
    if rerr:
        st.error(rerr)
        return
    responses = responses or {}
    if not responses.get("total"):
        st.info("이 Round 로 나간 요청이 아직 없다.")
        return

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("발송", responses.get("total"))
    r2.metric("회신", responses.get("responded"))
    r3.metric("미회신", responses.get("pending"))
    r4.metric("회신율", f"{round((responses.get('response_rate') or 0) * 100, 1)}%")
    if responses.get("avg_response_hours") is not None:
        st.caption(f"평균 회신 소요 {responses['avg_response_hours']}시간")

    items = responses.get("responses") or []
    rdf = pd.DataFrame([
        {
            "성명": i["name"], "팀": i["team"], "조직": i.get("org"),
            "이메일": i["email"], "회신": "✅" if i["responded"] else "⏳",
            "제출시각": str(i.get("submitted_at") or "")[:19],
            "소요(h)": i.get("response_hours"),
            "리마인더": i.get("last_reminder_level"),
            "슬롯": sum(
                1 for _ in ((i.get("payload") or {}).get("available_slots") or [])
            ),
        }
        for i in items
    ])
    st.dataframe(rdf, width="stretch", hide_index=True, height=380)

    c1, c2 = st.columns([1, 4])
    if c1.button("🔔 리마인더 1회차 실행", key="c_remind"):
        data, err = post_json(f"{COLLECTOR}/api/v1/reminders/run-cycle", {})
        if err:
            st.error(err)
        else:
            clear_caches()
            st.success(f"리마인더 {data.get('sent_count')}건 발송")
    c2.caption("규칙(D-3 · D-1 · 마감일 상급자 CC)에 해당하는 미회신자에게만 나간다.")

    st.divider()
    st.subheader("④ 가용성 집계 (5번 메뉴 입력)")
    summary, sumerr = fetch_json(f"{COLLECTOR}/api/v1/rounds/{round_id}/availability/summary")
    if sumerr:
        st.error(sumerr)
    elif summary:
        s1, s2, s3 = st.columns(3)
        s1.metric("초대", summary.get("invited"))
        s2.metric("회신", summary.get("responded"))
        s3.metric("총 가용 슬롯", summary.get("total_slots"))
        if summary.get("teams"):
            st.dataframe(pd.DataFrame(summary["teams"]), width="stretch", hide_index=True)

    include_pending = st.checkbox("미회신자도 표시", value=False, key="c_pending")
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
                "우선순위": row["priority"], "일일최대": row["max_daily"],
                "슬롯수": row["slot_count"], "회신": "✅" if row["responded"] else "⏳",
                "가용": ", ".join(
                    f"{day}({'/'.join(hours)})"
                    for day, hours in (row.get("availability") or {}).items()
                ),
            }
            for row in detail
        ])
        st.dataframe(ddf, width="stretch", hide_index=True, height=380)
    else:
        st.info("아직 회신된 가용성이 없다.")

    with st.expander("발송 내역 (06 notification-hub — 폼 링크 확인)"):
        history, herr = fetch_json(
            f"{NOTIFIER}/api/v1/notify/history", (("round_id", round_id), ("limit", 200))
        )
        if herr:
            st.error(herr)
        elif history and history.get("items"):
            st.dataframe(pd.DataFrame(history["items"]), width="stretch", hide_index=True)
        else:
            st.info("발송 기록이 없다.")


# ============================================================
# 5. 면접 일정 분배
# ============================================================
def render_timetable(assignments: list[dict]) -> None:
    """팀 × 요일 8슬롯 시간표 — 칸마다 '지원자 (면접관)' 을 적는다."""
    st.markdown("### 🗓️ 면접 시간표 (지원자 ↔ 면접 담당자)")
    c1, c2, c3, c4 = st.columns(4)
    start = c1.text_input("시작 시각", value="09:00", key="t_start")
    minutes = c2.number_input("면접(분)", 10, 120, SLOT_MINUTES, 5, key="t_min")
    rest = c3.number_input("휴식(분)", 0, 60, BREAK_MINUTES, 5, key="t_rest")
    per_day = c4.number_input("하루 슬롯", 1, 20, SLOTS_PER_DAY, key="t_perday")
    try:
        labels = slot_labels(start.strip(), int(per_day), int(minutes), int(rest))
    except ValueError:
        st.error("시작 시각은 HH:MM 형식으로 입력하세요. (예: 09:00)")
        return

    roster, _ = fetch_json(f"{SCHEDULER}/api/v1/interviewers")
    names = {
        row["interviewer_id"]: row.get("name") or row["interviewer_id"]
        for row in (roster or [])
    }

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
                    who = names.get(item.get("interviewer_id"), item.get("interviewer_id"))
                    row[team] = f"{item.get('applicant_name') or item.get('applicant_id')} ({who})"
            rows.append(row)
        for team in teams:
            overflow.extend((buckets.get((team, day)) or [])[int(per_day):])
        if day_index < len(days) - 1:
            rows.append({"구분": "", **blank})  # 요일 사이 빈 줄

    table = pd.DataFrame(rows, columns=["구분"] + teams)
    st.dataframe(table, width="stretch", hide_index=True,
                 height=min(38 * (len(table) + 1) + 3, 760))
    st.caption(
        f"{int(minutes)}분 면접 + {int(rest)}분 휴식 · 하루 {int(per_day)}슬롯 · "
        f"{len(days)}일 · {len(teams)}팀 · 배정 {len(assignments)}건"
    )
    if overflow:
        with st.expander(f"⚠ 하루 {int(per_day)}슬롯을 넘은 배정 {len(overflow)}건"):
            st.dataframe(pd.DataFrame(overflow), width="stretch", hide_index=True)
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
    c1.metric("배정", f"{sched.get('total_assigned')} / {sched.get('total_applicants')}")
    c2.metric("커버리지", f"{sched.get('coverage_pct')}%")
    c3.metric("하드 위반", sched.get("hard_violations"))
    c4.metric("규칙 준수", f"{rc.get('overall')}%")
    st.caption(
        f"round {sched.get('round_id')} · plan {sched.get('plan_id')} · "
        f"algorithm {sched.get('algorithm')} · status {sched.get('status')} · "
        f"생성 {sched.get('generated_at')} · {sched.get('elapsed_ms')}ms"
    )

    assignments = sched.get("assignments") or []
    if not assignments:
        st.warning("배정 결과가 비어 있습니다.")
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
    st.markdown("### 📈 분포")

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
        st.markdown("**팀 × 학력 교차 (고르게 섞였는지 확인)**")
        cross = pd.crosstab(df["team"], df["degree"])
        cross["합계"] = cross.sum(axis=1)
        st.dataframe(cross, width="stretch")
        st.bar_chart(pd.crosstab(df["team"], df["degree"]), height=300, stack=False)

    if {"day", "degree"} <= set(df.columns):
        st.markdown("**요일 × 학력 교차**")
        dd = pd.crosstab(df["day"], df["degree"])
        dd = dd.reindex([d for d in DAY_ORDER if d in dd.index])
        st.bar_chart(dd, height=300, stack=False)

    if "interviewer_id" in df:
        st.markdown("**면접관별 배정 건수 (부하 편중 확인)**")
        st.bar_chart(counts("interviewer_id"), height=280)

    # ---------------- 전체 배정 목록 ----------------
    st.markdown("### 🗒️ 전체 배정 목록")
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

    st.dataframe(shown, width="stretch", height=620, hide_index=True)
    st.caption(f"{len(shown)} / {len(df)}건 표시")
    st.download_button(
        "⬇ CSV 다운로드",
        shown.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"schedule_{sc_id[:8]}.csv",
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
            order = [c for c in ["day", "hour", "applicant_id", "applicant_name",
                                 "degree", "interviewer_id", "lock_level",
                                 "reason_tags"] if c in tdf.columns]
            tdf = tdf[order]
            sc = [c for c in ["day", "hour"] if c in order]
            st.dataframe(
                tdf.sort_values(sc) if sc else tdf,
                width="stretch", hide_index=True,
                height=min(38 * (len(tdf) + 1) + 3, 700),
            )

    # ---------------- 히트맵 ----------------
    st.markdown("### 🔥 요일 × 시간 히트맵")
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
        st.caption("칸의 숫자 = 해당 요일·시간대에 배정된 면접 건수")

    # ---------------- 규칙 준수 ----------------
    st.markdown("### 📐 규칙 준수 상세")
    rules, rerr = fetch_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}/rules")
    if rerr:
        st.error(rerr)
    else:
        rules = rules or {}
        labels_rule = {
            "rule1_grad_balance": "규칙1 학위 균형",
            "rule2_team_conflict": "규칙2 팀 충돌(HARD)",
            "rule3_vertical_group": "규칙3 수직 묶음",
            "rule4_first_slot": "규칙4 첫 슬롯",
        }
        for col, (key, label) in zip(st.columns(len(labels_rule)), labels_rule.items()):
            col.metric(label, f"{(rules.get(key) or {}).get('score', '-')}%")
        with st.expander("원본 JSON"):
            st.json(rules)


def render_scheduling() -> None:
    st.header("5️⃣ 면접 일정 분배")
    st.caption(
        "확정된 배포 명단(2번)과 면접관 가용성(3·4번)으로 시간표를 만든다. "
        "같은 팀 소속 면접관과 마주치지 않게 하는 것이 하드 제약이다."
    )
    if not need_round():
        return

    st.subheader("① 시간표 생성")
    c1, c2, c3 = st.columns([3, 1, 1])
    plan_id = c1.text_input(
        "Plan ID", value=st.session_state.get("plan_id") or "", key="s_plan"
    ).strip()
    algorithm = c2.selectbox("알고리즘", ["v5", "v4", "v3", "v2", "v1"], key="s_algo")
    c3.write("")
    run = c3.button("▶ 생성", type="primary", key="s_generate")

    picked = fetch_json(f"{SCHEDULER}/api/v1/interviewers/rounds/{round_id}")[0] or []
    st.caption(
        f"이번 회차 선별 면접관 {len(picked)}명"
        + (" — 선별이 없으면 등록된 전체 면접관으로 짠다." if not picked else "")
    )

    if run:
        if not plan_id:
            st.warning("Plan ID 가 필요하다 (2번 메뉴에서 배포안을 먼저 만드세요).")
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
                st.success(f"생성 완료 — schedule `{data.get('schedule_id')}`")

    st.divider()
    st.subheader("② 결과 확인")
    rounds = [r for r in fetch_rounds() if r["round_id"] == round_id]
    sc_id = st.session_state.get("schedule_id")
    if rounds:
        labels = [f"{r['at']} · {r['assigned']}명 · {r['schedule_id'][:8]}" for r in rounds]
        index = next((i for i, r in enumerate(rounds) if r["schedule_id"] == sc_id), 0)
        pick = st.selectbox("생성된 시간표", range(len(rounds)), index=index,
                            format_func=lambda i: labels[i], key="s_pick")
        sc_id = rounds[pick]["schedule_id"]

    manual = st.text_input("Schedule ID 직접 입력", value="", key="s_manual").strip()
    if manual:
        sc_id = manual

    if not sc_id:
        st.info("아직 생성된 시간표가 없다.")
        return

    st.caption(f"Schedule `{sc_id}`")
    a1, a2, a3 = st.columns([1, 1, 3])
    if a1.button("🧪 검증", key="s_validate"):
        data, err = post_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}/validate", {})
        if err:
            st.error(err)
        else:
            st.info(f"하드 위반 {len(data.get('hard_violations') or [])}건 · "
                    f"soft penalty {data.get('soft_penalty')}")
    if a2.button("🔒 확정(LOCK)", key="s_lock"):
        data, err = post_json(
            f"{SCHEDULER}/api/v1/schedules/{sc_id}/lock",
            {"lock_level": "LOCKED", "actor": actor},
        )
        if err:
            st.error(err)
        else:
            clear_caches()
            st.success(f"확정 완료 — 상태 {(data or {}).get('status')}")
    a3.caption("확정하면 05(재편성)가 해당 배정을 함부로 옮기지 못한다.")

    render_schedule_body(sc_id)


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
if menu == MENUS[0]:
    render_versions()
elif menu == MENUS[1]:
    render_distribution()
elif menu == MENUS[2]:
    render_interviewers()
elif menu == MENUS[3]:
    render_collection()
elif menu == MENUS[4]:
    render_scheduling()
else:
    render_admin()
