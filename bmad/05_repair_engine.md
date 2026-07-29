# 📦 Service 05 — Repair Engine

> **필수 참조**: `00_SHARED_CONTRACT.md`
> **담당 단계**: 5️⃣ 면접 일정 재편성 · 노쇼 대응
> **포트**: 8005 · **DB 스키마**: `repair_db`

---

## B — Business Context

### 해결하는 문제
- 노쇼·취소 발생 시 전체 시간표 재편성 → 다시 16~48시간 소요
- 재편성 알고리즘이 하드 제약을 재검증하지 않으면 위반 발생 (v3에서 확인)
- HR 담당자에게 대안이 제시되지 않아 즉흥 판단으로 처리

### 비즈니스 가치
- 안전 재편성으로 하드 제약 위반 0건 유지 (v3.1 검증됨)
- Plan A/B/C 3가지 대안 자동 제시 → HR은 클릭만
- 노쇼자 재예약률 100% (오버부킹 없이)
- 3단계 락 시스템으로 이미 안내된 지원자 절대 안 흔들림

### 성공 기준
- 재편성 5초 이내
- 하드 위반 0건
- Plan 3가지 자동 생성
- LOCKED 상태 배정은 절대 변경 안 됨

---

## M — Model

### 테이블 스키마

```sql
CREATE TABLE repair_events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id        VARCHAR(32) NOT NULL,
    schedule_id     UUID NOT NULL,        -- Service 04의 schedule_id
    trigger_type    VARCHAR(32),          -- 'noshow'|'cancel_applicant'|'cancel_interviewer'|'change'
    trigger_target  VARCHAR(64),          -- applicant_id or interviewer_id
    reported_at     TIMESTAMPTZ DEFAULT NOW(),
    reported_by     VARCHAR(64),
    status          VARCHAR(16)           -- 'pending'|'resolved'|'deferred'
);

CREATE TABLE repair_plans (
    plan_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID REFERENCES repair_events(event_id),
    plan_type       VARCHAR(16),          -- 'A_safe'|'B_defer'|'C_cross_team'
    rebooked_count  INTEGER,
    deferred_count  INTEGER,
    hard_violations INTEGER,
    soft_penalty    INTEGER,
    plan_detail     JSONB,                -- 변경될 배정 리스트
    generated_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE selected_plans (
    selection_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id        UUID REFERENCES repair_events(event_id),
    plan_id         UUID REFERENCES repair_plans(plan_id),
    selected_by     VARCHAR(64),
    selected_at     TIMESTAMPTZ DEFAULT NOW(),
    applied_at      TIMESTAMPTZ
);

CREATE TABLE lock_map (
    schedule_id     UUID,
    applicant_id    VARCHAR(32),
    lock_level      VARCHAR(16),          -- DRAFT|CONFIRMED|LOCKED
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (schedule_id, applicant_id)
);
```

---

## A — API

### 이벤트 통보 (외부 트리거 진입점)
```
POST /api/v1/repair/noshow
body:
{
  "round_id": "R2026-Q3-01",
  "schedule_id": "...",
  "noshow_applicant_ids": ["3339449", "3814483"],
  "reported_by": "HR김민지"
}

response 202:
{"data": {"event_id":"...", "status":"pending"}}
```

```
POST /api/v1/repair/cancel
body:
{
  "round_id": "...",
  "schedule_id": "...",
  "cancel_type": "applicant"|"interviewer",
  "target_id": "...",
  "reason": "..."
}
```

### 대안 시나리오 조회
```
GET /api/v1/repair/plans/{event_id}

response 200:
{
  "data": {
    "plans": [
      {"plan_id":"...", "type":"A_safe",
       "rebooked":13, "deferred":0, "hard":0, "soft":21,
       "description":"예비 슬롯 활용 · 팀 일치"},
      {"plan_id":"...", "type":"B_defer",
       "rebooked":0, "deferred":13, "hard":0, "soft":19,
       "description":"이번 회차 유지 · 다음 회차 이월"},
      {"plan_id":"...", "type":"C_cross_team",
       "rebooked":13, "deferred":0, "hard":0, "soft":23,
       "cross_team_count":11, "warning":"Cross-team 사례 11건",
       "description":"cross-team 재예약 허용"}
    ]
  }
}
```

### Plan 선택 · 실행
```
POST /api/v1/repair/plans/{event_id}/select
body: {"plan_id":"...", "selected_by":"HR김민지"}

response 200:
{"data": {"applied":true, "affected_assignments":13}}
```

### 감사 로그
```
GET /api/v1/repair/audit/{round_id}

response 200:
{
  "data": [
    {"event_id":"...", "trigger_type":"noshow",
     "selected_plan":"A_safe", "selected_by":"HR김민지",
     "applied_at":"...", "affected_count":13}
  ]
}
```

### 락 관리
```
GET  /api/v1/repair/locks/{schedule_id}
POST /api/v1/repair/locks/upgrade
body: {"schedule_id":"...", "applicant_ids":[...], "new_level":"LOCKED"}
```

---

## D — Design

### 이벤트 발행
- `REPAIR_EXECUTED`: Plan 선택·적용 완료
- `PARTICIPANT_DEFERRED`: 다음 회차 이월 결정
- `SLOT_REOPENED`: 사용 안 된 슬롯 반환

### 구독 이벤트
- `SCHEDULE_LOCKED`: 재편성 대상 시간표 정보 수신
- (외부에서 노쇼 통보는 API `POST /repair/noshow`로)

### 안전 재편성 알고리즘 (v3.1)
```python
def repair_safely(original_assignments, noshow_ids, reserved_slots,
                  interviewers, applicants, lock_map):
    # 1. LOCKED 상태 노쇼자는 즉시 다음 회차 이월
    # 2. 나머지 노쇼자에게 예비 슬롯 재예약 시도
    # 3. 각 후보 슬롯마다 check_hard_constraints 재검증
    # 4. 위반 발생 슬롯은 스킵
    # 5. 팀 일치 우선, 실패 시 deferred로
```

### Plan A/B/C 자동 생성
- **Plan A (Safe)**: 예비 슬롯 · 팀 일치 · 하드 제약 검증
- **Plan B (Defer)**: 노쇼자 전원 다음 회차 이월
- **Plan C (Cross-team)**: 팀 불일치 허용, 유연성 우선

### 3단계 락 시스템
```python
LOCK_LEVELS = {"DRAFT": 0, "CONFIRMED": 1, "LOCKED": 2}
# 재편성 시 LOCKED > 재편성 불가
# CONFIRMED > 페널티 부여 후 이동 허용
# DRAFT > 자유롭게 이동
```

### 프로젝트 구조
```
repair-engine/
├── app/
│   ├── main.py
│   ├── api/{repair.py, plans.py, locks.py, audit.py}
│   ├── domain/{repair_event.py, repair_plan.py}
│   ├── services/
│   │   ├── safe_repair.py       # v3.1 로직
│   │   ├── plan_generator.py    # A/B/C 생성
│   │   ├── constraint_recheck.py
│   │   └── scheduler_client.py  # Service 04에서 스케줄 로드
│   ├── infrastructure/{db.py, event_bus.py}
│   └── events.py
```

### 테스트 요구사항
- `test_safe_repair_zero_violation`: 노쇼 20% 상황에서도 하드 위반 0
- `test_locked_untouched`: LOCKED 지원자는 재편성 시 자동 defer
- `test_plans_all_three`: A/B/C 3개 모두 생성됨
- `test_plan_a_prefers_same_team`: Plan A는 항상 팀 일치 우선

### 완료 판정 체크리스트
- [ ] 노쇼 13명 상황에서 재편성 5초 이내
- [ ] Plan A/B/C 자동 생성 확인
- [ ] LOCKED 배정은 어떤 Plan에서도 이동 안 됨
- [ ] `REPAIR_EXECUTED` 이벤트 발행 확인
- [ ] 감사 로그가 모든 재편성 기록
