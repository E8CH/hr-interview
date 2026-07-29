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

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🩺 헬스체크", "🚀 시나리오 실행", "📜 이벤트 타임라인", "🗃️ DB 조회", "📊 KPI 대시보드"
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
    if st.button("📊 KPI 조회"):
        try:
            r = httpx.get(f"{AUDIT}/api/v1/dashboard/kpi", timeout=5.0)
            if r.status_code == 200:
                st.json(r.json())
            else:
                st.warning(f"status {r.status_code}")
        except Exception as e:
            st.error(str(e))
    st.divider()
    if st.button("🏢 조직 응답 통계"):
        try:
            r = httpx.get(f"{AUDIT}/api/v1/dashboard/organizations", timeout=5.0)
            if r.status_code == 200:
                st.json(r.json())
        except Exception as e:
            st.error(str(e))
