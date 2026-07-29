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

VERSION_MANAGER = "http://localhost:8001"
DISTRIBUTOR = "http://localhost:8002"
SCHEDULER = "http://localhost:8004"
REPAIR_ENGINE = "http://localhost:8005"
AUDIT = "http://localhost:8007"


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
            r = httpx.get(f"http://localhost:{port}/healthz", timeout=0.5)
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
                    r = httpx.post(
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
                        r = httpx.post(
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
                        r = httpx.post(
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
                                return httpx.post(
                                    f"{REPAIR_ENGINE}/api/v1/repair/noshow",
                                    json={
                                        "round_id": round_id,
                                        "schedule_id": schedule_id,
                                        "noshow_applicant_ids": ids,
                                        "reported_by": actor,
                                    },
                                    timeout=30.0,
                                )

                            r = httpx.get(f"{SCHEDULER}/api/v1/schedules/{schedule_id}", timeout=15.0)
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
                                    locks = httpx.get(
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
                try:
                    events = []
                    # 이벤트 포워딩은 비동기라 잠깐 재시도한다
                    for _ in range(10):
                        r = httpx.get(
                            f"{AUDIT}/api/v1/audit/timeline",
                            params={"round_id": round_id},
                            timeout=5.0,
                        )
                        data = unwrap(r)
                        events = data if isinstance(data, list) else (data or {}).get("events", [])
                        if events:
                            break
                        time.sleep(0.3)
                    add(f"  → status {r.status_code}")
                    add(f"  events: {len(events)}")
                    for ev in events:
                        add(f"    - {ev.get('event_type')} ({ev.get('producer')})")
                except Exception as e:
                    add(f"  ❌ {e}")

                add("\n❌ 실패한 단계가 있습니다" if failed else "\n✅ 완료")
                st.session_state["last_round_id"] = round_id
                st.session_state["last_plan_id"] = plan_id
                st.session_state["last_schedule_id"] = schedule_id

# ============================================================
# Tab 6: 시간표 / 배정 결과
# ============================================================
with tab6:
    st.subheader("최종 시간표 · 배정 결과")
    import pandas as pd

    default_schedule = st.session_state.get(
        "last_schedule_id", st.session_state.get("last_round_id", "")
    )
    key_input = st.text_input(
        "Schedule ID 또는 Round ID",
        value=default_schedule,
        key="sc_id",
        help=(
            "시나리오 실행 후 자동으로 채워진다. Round ID(R2026-…)를 넣으면 "
            "07 타임라인의 SCHEDULE_GENERATED 이벤트에서 Schedule ID를 찾아 조회한다."
        ),
    )

    def resolve_schedule_id(text: str):
        """Round ID 를 넣어도 되게 한다.

        Schedule ID 는 UUID(16진수)라 절대 'R' 로 시작하지 않으므로 이걸로 구분한다.
        """
        text = (text or "").strip()
        if not text or not text.upper().startswith("R"):
            return text, None  # Schedule ID(UUID) 로 간주
        try:
            r = httpx.get(
                f"{AUDIT}/api/v1/audit/timeline",
                params={"round_id": text, "event_type": "SCHEDULE_GENERATED"},
                timeout=10.0,
            )
        except Exception as e:
            return text, f"Round ID 조회 실패: {e}"
        if r.status_code != 200:
            return text, f"Round ID 조회 실패 (status {r.status_code})"
        data = unwrap(r)
        events = data if isinstance(data, list) else (data or {}).get("events", [])
        for ev in reversed(events):  # 재생성된 경우 가장 최근 것
            sid = (ev.get("payload") or {}).get("schedule_id")
            if sid:
                return sid, f"Round {text} → Schedule {sid}"
        return text, (
            f"Round {text} 에 SCHEDULE_GENERATED 이벤트가 없습니다. "
            "3단계(스케줄 생성)까지 실행됐는지 확인하세요."
        )

    if not key_input:
        st.info("시나리오 탭에서 3단계까지 실행하면 Schedule ID 가 자동으로 채워집니다.")
    elif st.button("🗓️ 시간표 조회", type="primary"):
        sc_id, note = resolve_schedule_id(key_input)
        if note:
            (st.caption if sc_id != key_input else st.warning)(note)
        try:
            r = httpx.get(f"{SCHEDULER}/api/v1/schedules/{sc_id}", timeout=20.0)
        except Exception as e:
            st.error(str(e))
            r = None

        if r is None:
            pass
        elif r.status_code != 200:
            st.error(f"status {r.status_code}: {error_text(r)}")
        else:
            sched = unwrap(r) or {}
            rc = sched.get("rule_compliance") or {}

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("배정", f"{sched.get('total_assigned')} / {sched.get('total_applicants')}")
            c2.metric("커버리지", f"{sched.get('coverage_pct')}%")
            c3.metric("하드 위반", sched.get("hard_violations"))
            c4.metric("규칙 준수", f"{rc.get('overall')}%")
            st.caption(
                f"round {sched.get('round_id')} · plan {sched.get('plan_id')} · "
                f"algorithm {sched.get('algorithm')} · status {sched.get('status')} · "
                f"{sched.get('elapsed_ms')}ms"
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
                df = df[cols + [c for c in df.columns if c not in cols and c != "assignment_id"]]

                view = st.radio(
                    "보기", ["전체 목록", "팀별", "요일 × 시간 히트맵", "규칙 상세"],
                    horizontal=True, key="sc_view",
                )

                if view == "전체 목록":
                    teams = sorted(df["team"].dropna().unique()) if "team" in df else []
                    pick = st.multiselect("팀 필터", teams, default=list(teams), key="sc_teams")
                    shown = df[df["team"].isin(pick)] if teams else df
                    sort_cols = [c for c in ["team", "day", "hour"] if c in shown.columns]
                    if sort_cols:
                        shown = shown.sort_values(sort_cols)
                    st.dataframe(shown, use_container_width=True, hide_index=True)
                    st.caption(f"총 {len(shown)}건")
                    st.download_button(
                        "⬇ CSV 다운로드",
                        shown.to_csv(index=False).encode("utf-8-sig"),
                        file_name=f"schedule_{sc_id[:8]}.csv",
                        mime="text/csv",
                    )

                elif view == "팀별":
                    rt = httpx.get(f"{SCHEDULER}/api/v1/schedules/{sc_id}/by-team", timeout=20.0)
                    teams = (unwrap(rt) or {}).get("teams", {}) if rt.status_code == 200 else {}
                    if not teams:
                        st.error(f"status {rt.status_code}: {error_text(rt)}")
                    for team, rows in teams.items():
                        with st.expander(f"{team} — {len(rows)}명", expanded=True):
                            tdf = pd.DataFrame(rows)
                            order = [c for c in ["day", "hour", "applicant_id", "applicant_name",
                                                 "degree", "interviewer_id", "lock_level",
                                                 "reason_tags"] if c in tdf.columns]
                            st.dataframe(
                                tdf[order].sort_values([c for c in ["day", "hour"] if c in order]),
                                use_container_width=True, hide_index=True,
                            )

                elif view == "요일 × 시간 히트맵":
                    rh = httpx.get(f"{SCHEDULER}/api/v1/schedules/{sc_id}/heatmap", timeout=20.0)
                    if rh.status_code != 200:
                        st.error(f"status {rh.status_code}: {error_text(rh)}")
                    else:
                        hm = unwrap(rh) or {}
                        grid = hm.get("grid", {})
                        hours = hm.get("hours", [])
                        hdf = pd.DataFrame(
                            [[grid.get(d, {}).get(h, 0) for h in hours] for d in hm.get("days", [])],
                            index=hm.get("days", []), columns=hours,
                        )
                        try:  # 색 그라데이션은 matplotlib 이 있을 때만
                            st.dataframe(
                                hdf.style.background_gradient(cmap="Blues", axis=None),
                                use_container_width=True,
                            )
                        except ImportError:
                            st.dataframe(hdf, use_container_width=True)
                        st.caption("칸의 숫자 = 해당 요일·시간대에 배정된 면접 건수")

                else:  # 규칙 상세
                    rr = httpx.get(f"{SCHEDULER}/api/v1/schedules/{sc_id}/rules", timeout=20.0)
                    if rr.status_code != 200:
                        st.error(f"status {rr.status_code}: {error_text(rr)}")
                    else:
                        rules = unwrap(rr) or {}
                        labels = {
                            "rule1_grad_balance": "규칙1 학위 균형",
                            "rule2_team_conflict": "규칙2 팀 충돌(HARD)",
                            "rule3_vertical_group": "규칙3 수직 묶음",
                            "rule4_first_slot": "규칙4 첫 슬롯",
                        }
                        cols = st.columns(len(labels))
                        for col, (key, label) in zip(cols, labels.items()):
                            col.metric(label, f"{(rules.get(key) or {}).get('score', '-')}%")
                        st.json(rules)

# ============================================================
# Tab 3: Timeline
# ============================================================
with tab3:
    st.subheader("이벤트 타임라인")
    default_round = st.session_state.get("last_round_id", "R2026-TEST-001")
    tl_round = st.text_input("조회할 Round ID", value=default_round, key="tl_round")
    if st.button("🔍 조회"):
        try:
            r = httpx.get(
                f"{AUDIT}/api/v1/audit/timeline",
                params={"round_id": tl_round}, timeout=5.0
            )
            if r.status_code == 200:
                data = unwrap(r)
                events = data if isinstance(data, list) else (data or {}).get("events", [])
                if events:
                    import pandas as pd
                    df = pd.DataFrame(events)
                    st.dataframe(df, use_container_width=True)
                    st.caption(f"총 {len(events)}건")
                else:
                    st.info("해당 Round의 이벤트가 없습니다.")
            else:
                st.error(f"status {r.status_code}: {error_text(r)}")
        except Exception as e:
            st.error(str(e))

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
    st.dataframe(pd.DataFrame(rows), use_container_width=True)

# ============================================================
# Tab 5: KPI Dashboard
# ============================================================
with tab5:
    st.subheader("KPI 대시보드")
    import pandas as pd

    # 07의 dashboard 엔드포인트는 전부 round_id 가 필수다 (없으면 422).
    kpi_round = st.text_input(
        "Round ID",
        value=st.session_state.get("last_round_id", "R2026-TEST-001"),
        key="kpi_round",
    )

    def fetch(path: str):
        r = httpx.get(f"{AUDIT}{path}", params={"round_id": kpi_round}, timeout=10.0)
        if r.status_code != 200:
            st.error(f"status {r.status_code}: {error_text(r)}")
            return None
        return unwrap(r)

    if st.button("📊 KPI 조회", type="primary"):
        kpi = fetch("/api/v1/dashboard/kpi")
        if isinstance(kpi, dict) and kpi:
            keys = list(kpi.keys())
            for row_start in range(0, len(keys), 4):
                for col, key in zip(st.columns(4), keys[row_start:row_start + 4]):
                    col.metric(key.replace("_", " "), kpi[key])
            st.json(kpi)
        elif kpi is not None:
            st.info("해당 Round의 KPI 데이터가 없습니다.")

    st.divider()
    if st.button("🏢 조직 응답 통계"):
        orgs = fetch("/api/v1/dashboard/organizations")
        if orgs:
            st.dataframe(pd.DataFrame(orgs), use_container_width=True, hide_index=True)
        elif orgs is not None:
            st.info("조직 응답 데이터가 없습니다. (03 회신 수집 전이면 비어 있는 게 정상)")

    st.divider()
    if st.button("⚠ 위험 신호"):
        risks = fetch("/api/v1/dashboard/risks")
        if risks:
            st.dataframe(pd.DataFrame(risks), use_container_width=True, hide_index=True)
        elif risks is not None:
            st.success("감지된 위험 신호가 없습니다.")

    st.divider()
    if st.button("📄 라운드 리포트"):
        try:
            r = httpx.get(f"{AUDIT}/api/v1/reports/rounds/{kpi_round}", timeout=10.0)
        except Exception as e:
            st.error(str(e))
        else:
            if r.status_code != 200:
                st.error(f"status {r.status_code}: {error_text(r)}")
            else:
                rep = unwrap(r) or {}
                if rep.get("phases"):
                    st.dataframe(pd.DataFrame(rep["phases"]), use_container_width=True, hide_index=True)
                st.json(rep)
