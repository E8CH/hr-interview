"""
HR Interview System - Streamlit Test Console (v3)

실제 서비스 계약(openapi.json)에 맞춘 시나리오 실행 콘솔.
    - Service 01 register 는 multipart/form-data (file + round_id + kind + actor)
    - Service 02 plan 은 master_version_id 필수
    - Service 04 generate 는 plan_id 필수 (null 불가)
    - 모든 응답은 {"data": ..., "error": ...} 봉투

Usage: streamlit run tools/test_console.py
"""
import time
from pathlib import Path

import httpx
import streamlit as st

st.set_page_config(page_title="HR Interview System Console", page_icon="🎯", layout="wide")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MASTER = PROJECT_ROOT / "docs" / "취합파일.xlsx"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# ============================================================
# 서비스/엔드포인트 정의 (실제 openapi.json 기반)
# ============================================================
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
SCHEDULER = "http://127.0.0.1:8004"
REPAIR_ENGINE = "http://127.0.0.1:8005"
AUDIT = "http://127.0.0.1:8007"


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


# ============================================================
# 조회 헬퍼 (캐시) — 탭 어디서든 쓸 수 있도록 탭 정의보다 위에 둔다
# ============================================================
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


@st.cache_data(ttl=60, show_spinner=False)
def fetch_round_ids():
    """감사 로그에 남은 Round 목록(최근 순). ID 를 타이핑하지 않게 하기 위한 것."""
    try:
        r = http().post(f"{AUDIT}/api/v1/audit/query", json={"limit": 300}, timeout=10.0)
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


def round_selector(key: str, label: str = "Round"):
    """최근 Round 를 고르는 셀렉트박스. 목록이 비면 직접 입력으로 넘어간다."""
    ids = fetch_round_ids()
    last = st.session_state.get("last_round_id")
    if not ids:
        return st.text_input(f"{label} ID", value=last or "", key=f"{key}_manual")
    idx = ids.index(last) if last in ids else 0
    col_sel, col_btn = st.columns([5, 1])
    picked = col_sel.selectbox(label, ids, index=idx, key=key)
    col_btn.write("")
    if col_btn.button("🔄", key=f"{key}_refresh", help="Round 목록 새로고침"):
        fetch_round_ids.clear()
        fetch_json.clear()
        st.rerun()
    return picked


@st.cache_data(ttl=60, show_spinner=False)
def fetch_rounds():
    """스케줄이 생성된 Round 목록 — 직접 입력하지 않아도 고르게 한다."""
    try:
        r = http().post(
            f"{AUDIT}/api/v1/audit/query",
            json={"event_types": ["SCHEDULE_GENERATED"], "limit": 100},
            timeout=10.0,
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

st.title("🎯 HR Interview System - Test Console")
st.caption("7개 마이크로서비스 통합 테스트 콘솔 (v3 — 실제 서비스 계약 반영)")

tab1, tab2, tab6, tab3, tab4, tab5 = st.tabs([
    "🩺 헬스체크", "🚀 시나리오 실행", "🗓️ 시간표 / 배정",
    "📜 이벤트 타임라인", "🗃️ DB 조회", "📊 KPI 대시보드"
])

# ============================================================
# Tab 1: Health check
# ============================================================
with tab1:
    st.subheader("서비스 헬스체크")
    if st.button("🔄 전체 새로고침", type="primary"):
        st.rerun()

    cols = st.columns(4)
    ok_count = 0
    for idx, (name, port) in enumerate(SERVICES):
        col = cols[idx % 4]
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

# ============================================================
# Tab 2: Scenario
# ============================================================
with tab2:
    st.subheader("시나리오 실행")
    scenario = st.selectbox("시나리오 선택", ["Happy Path (정상 흐름)", "No-show Repair (노쇼 재편성)"])

    uploaded = st.file_uploader("마스터 엑셀 업로드 (선택)", type=["xlsx"])
    if uploaded is None:
        if DEFAULT_MASTER.exists():
            st.caption(f"업로드가 없으면 기본 파일을 사용합니다: `{DEFAULT_MASTER.name}`")
        else:
            st.warning(f"기본 마스터 파일이 없습니다: {DEFAULT_MASTER} — 파일을 업로드하세요.")

    round_id = st.text_input("Round ID", value=f"R2026-TEST-{int(time.time()) % 1000:03d}")
    actor = st.text_input("Actor (등록자)", value="test_console")

    if st.button("▶ 실행", type="primary"):
        # 업로드 파일 확보 (없으면 docs/취합파일.xlsx 폴백)
        if uploaded is not None:
            file_name, file_bytes = uploaded.name, uploaded.getvalue()
        elif DEFAULT_MASTER.exists():
            file_name, file_bytes = DEFAULT_MASTER.name, DEFAULT_MASTER.read_bytes()
        else:
            file_name, file_bytes = None, None

        if not file_bytes:
            st.error("마스터 엑셀 파일이 필요합니다. 파일을 업로드한 뒤 다시 실행하세요.")
        else:
            with st.spinner("시나리오 실행 중..."):
                log = st.empty()
                messages = []

                def add(msg):
                    messages.append(msg)
                    log.code("\n".join(messages))

                version_id = None
                plan_id = None
                schedule_id = None
                failed = False

                # --- Step 1: 마스터 버전 등록 (multipart/form-data) ---
                add(f"[1/4] Version Manager - Round {round_id} 등록 ({file_name})")
                try:
                    r = http().post(
                        f"{VERSION_MANAGER}/api/v1/versions/register",
                        files={"file": (file_name, file_bytes, XLSX_MIME)},
                        data={"round_id": round_id, "kind": "master", "actor": actor},
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
                    try:
                        r = http().post(
                            f"{DISTRIBUTOR}/api/v1/distribute/plan",
                            json={
                                "round_id": round_id,
                                "master_version_id": version_id,
                                "created_by": actor,
                            },
                            timeout=60.0,
                        )
                        add(f"  → status {r.status_code}")
                        if r.status_code < 300:
                            data = unwrap(r) or {}
                            plan_id = data.get("plan_id")
                            add(f"  plan_id: {plan_id}")
                            add(f"  배정: {data.get('total_applicants')}명 / 팀별 {data.get('team_counts')}")
                        else:
                            failed = True
                            add(f"  ❌ {error_text(r)}")
                    except Exception as e:
                        failed = True
                        add(f"  ❌ {e}")

                # --- Step 3: 시간표 생성 (plan_id 필수) ---
                add("[3/4] Scheduler - 일정 생성")
                if not plan_id:
                    add("  ⏭ 건너뜀 — 2단계에서 plan_id 를 얻지 못함")
                else:
                    try:
                        r = http().post(
                            f"{SCHEDULER}/api/v1/schedules/generate",
                            json={
                                "round_id": round_id,
                                "plan_id": plan_id,
                                "algorithm": "v5",
                                "generated_by": actor,
                            },
                            timeout=60.0,
                        )
                        add(f"  → status {r.status_code}")
                        if r.status_code < 300:
                            data = unwrap(r) or {}
                            schedule_id = data.get("schedule_id")
                            add(f"  schedule_id: {schedule_id}")
                            add(
                                f"  배정 {data.get('total_assigned')}/{data.get('total_applicants')}"
                                f" (coverage {data.get('coverage_pct')}%,"
                                f" 하드위반 {data.get('hard_violations')})"
                            )
                        else:
                            failed = True
                            add(f"  ❌ {error_text(r)}")
                    except Exception as e:
                        failed = True
                        add(f"  ❌ {e}")

                # --- Step 3.5: 노쇼 재편성 시나리오 ---
                if scenario.startswith("No-show"):
                    add("[3.5] Repair Engine - 노쇼 재편성")
                    if not schedule_id:
                        add("  ⏭ 건너뜀 — schedule_id 없음")
                    else:
                        try:
                            def report_noshow(ids):
                                return http().post(
                                    f"{REPAIR_ENGINE}/api/v1/repair/noshow",
                                    json={
                                        "round_id": round_id,
                                        "schedule_id": schedule_id,
                                        "noshow_applicant_ids": ids,
                                        "reported_by": actor,
                                    },
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
                                # 04 의 지원자 ID 가 없으므로, 05 가 적재한 스냅샷의 ID 로 재시도한다.
                                if r.status_code == 404:
                                    add("  ↩ 05 mock 시간표에 없는 ID — 05 스냅샷 기준으로 재시도")
                                    locks = http().get(
                                        f"{REPAIR_ENGINE}/api/v1/repair/locks/{schedule_id}",
                                        timeout=15.0,
                                    )
                                    rows = (unwrap(locks) or {}).get("locks", [])
                                    fallback = [
                                        row["applicant_id"] for row in rows
                                        if row.get("lock_level") != "LOCKED"
                                    ][:2]
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
                # 포워딩이 비동기라, 앞 단계가 성공시킨 이벤트가 다 도착할 때까지
                # 기다린다. 첫 1건에서 멈추면 나머지가 오기 전에 끝나버린다.
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
                        r = http().get(
                            f"{AUDIT}/api/v1/audit/timeline",
                            params={"round_id": round_id},
                            timeout=5.0,
                        )
                        data = unwrap(r)
                        events = data if isinstance(data, list) else (data or {}).get("events", [])
                        if expected <= {ev.get("event_type") for ev in events}:
                            break
                        time.sleep(0.2)
                    add(f"  → status {r.status_code}")
                    add(f"  events: {len(events)}")
                    for ev in events:
                        add(f"    - {ev.get('event_type')} ({ev.get('producer')})")
                    missing = expected - {ev.get("event_type") for ev in events}
                    if missing:
                        add(f"  ⚠ 도착하지 않은 이벤트: {', '.join(sorted(missing))}")
                except Exception as e:
                    add(f"  ❌ {e}")

                add("\n❌ 실패한 단계가 있습니다" if failed else "\n✅ 완료")
                st.session_state["last_round_id"] = round_id
                st.session_state["last_plan_id"] = plan_id
                st.session_state["last_schedule_id"] = schedule_id
                # 방금 만든 Round 가 다른 탭 목록에 바로 뜨도록 캐시를 비운다
                fetch_round_ids.clear()
                fetch_rounds.clear()
                fetch_json.clear()

# ============================================================
# Tab 6: 시간표 / 배정 결과
# ============================================================
# 렌더링을 버튼 안에 넣으면 필터를 건드릴 때마다 rerun 되면서 화면이 비어버린다.
# 그래서 조회는 캐시된 함수로 빼고, 본문은 항상 그린다.




with tab6:
    import pandas as pd

    st.subheader("최종 시간표 · 배정 결과")

    rounds = fetch_rounds()
    last_round = st.session_state.get("last_round_id")
    labels = [f"{r['round_id']}  ·  {r['at']}  ·  {r['assigned']}명" for r in rounds]

    sc_id = None
    if rounds:
        default_idx = next(
            (i for i, r in enumerate(rounds) if r["round_id"] == last_round), 0
        )
        c_sel, c_btn = st.columns([5, 1])
        pick = c_sel.selectbox(
            "Round (스케줄이 생성된 회차만 표시 — 최근 순)",
            range(len(rounds)),
            index=default_idx,
            format_func=lambda i: labels[i],
            key="sc_round_pick",
        )
        sc_id = rounds[pick]["schedule_id"]
        c_btn.write("")
        if c_btn.button("🔄 새로고침", help="캐시를 비우고 다시 조회"):
            fetch_json.clear()
            fetch_rounds.clear()
            st.rerun()
        st.caption(f"Round {rounds[pick]['round_id']} → Schedule `{sc_id}`")
    else:
        st.info("아직 생성된 스케줄이 없습니다. 시나리오 탭에서 3단계까지 실행하세요.")

    with st.expander("Schedule ID 직접 입력", expanded=not rounds):
        manual = st.text_input("Schedule ID (UUID)", value="", key="sc_manual")
        if manual.strip():
            sc_id = manual.strip()

    if sc_id:
        sched, err = fetch_json(f"{SCHEDULER}/api/v1/schedules/{sc_id}")
        if err:
            st.error(err)
        else:
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
            else:
                df = pd.DataFrame(assignments)
                cols = [
                    c for c in [
                        "applicant_id", "applicant_name", "team", "degree",
                        "day", "hour", "interviewer_id", "lock_level", "reason_tags",
                    ] if c in df.columns
                ]
                df = df[cols + [c for c in df.columns
                                if c not in cols and c not in ("assignment_id",)]]

                # ---------------- 분포 그래프 ----------------
                st.markdown("### 📈 분포")
                DAY_ORDER = ["월", "화", "수", "목", "금", "토", "일"]

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
                    st.bar_chart(
                        pd.crosstab(df["team"], df["degree"]), height=300, stack=False
                    )

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
                pick_teams = f1.multiselect("팀", teams, default=list(teams), key="f_team")
                pick_days = f2.multiselect("요일", days, default=list(days), key="f_day")
                pick_degs = f3.multiselect("학력", degrees, default=list(degrees), key="f_deg")

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
                        st.dataframe(
                            hdf.style.background_gradient(cmap="Blues", axis=None),
                            width="stretch",
                        )
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
                    for col, (key, label) in zip(st.columns(len(labels_rule)),
                                                 labels_rule.items()):
                        col.metric(label, f"{(rules.get(key) or {}).get('score', '-')}%")
                    with st.expander("원본 JSON"):
                        st.json(rules)

# ============================================================
# Tab 3: Timeline
# ============================================================
with tab3:
    import pandas as pd

    st.subheader("이벤트 타임라인")
    tl_round = round_selector("tl_round")
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
                st.dataframe(
                    df, width="stretch", hide_index=True,
                    height=min(38 * (len(df) + 1) + 3, 620),
                )
                st.caption(f"총 {len(events)}건")

# ============================================================
# Tab 4: DB inspection
# ============================================================
with tab4:
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
    import pandas as pd
    rows = []
    for svc, path in db_map.items():
        if path.exists():
            size_kb = path.stat().st_size / 1024
            rows.append({"service": svc, "exists": "✅", "size_KB": f"{size_kb:.1f}", "path": str(path)})
        else:
            rows.append({"service": svc, "exists": "❌", "size_KB": "-", "path": str(path)})
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

# ============================================================
# Tab 5: KPI Dashboard
# ============================================================
with tab5:
    import pandas as pd

    st.subheader("KPI 대시보드")

    # 07의 dashboard 엔드포인트는 전부 round_id 가 필수다 (없으면 422).
    kpi_round = round_selector("kpi_round")

    if kpi_round:
        kpi, err = fetch_json(f"{AUDIT}/api/v1/dashboard/kpi", (("round_id", kpi_round),))
        if err:
            st.error(err)
        elif isinstance(kpi, dict) and kpi:
            keys = list(kpi.keys())
            for row_start in range(0, len(keys), 4):
                for col, key in zip(st.columns(4), keys[row_start:row_start + 4]):
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
                st.dataframe(pd.DataFrame(rep["phases"]), width="stretch", hide_index=True)
            with st.expander("원본 JSON"):
                st.json(rep)
