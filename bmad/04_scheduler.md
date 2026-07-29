# 📦 Service 04 — Scheduler

> **필수 참조**: `00_SHARED_CONTRACT.md`
> **담당 단계**: 4️⃣ 면접 담당자 배치
> **포트**: 8004 · **DB 스키마**: `sched_db`

---

## B — Business Context

### 해결하는 문제
- 500명 규모 배치를 사람이 엑셀로 짜서 16~48시간 소요
- PPT 4대 규칙(요일 분산·팀 중복·세로 연속·첫 타임)이 서로 상충
- 리더 과부하, 학사/대학원 요일 편중 등 품질 문제

### 비즈니스 가치
- 2단계 계층적 배치로 4대 규칙 준수율 90% 달성 (v4 검증됨)
- 세로 연속 배치 100% · 시간대 균등 완벽 · 하드 위반 0건
- 실행 시간 1초 미만
- Trade-off: 커버리지 68% → v5에서 90%까지 복원 필요

### 성공 기준
- 88명 배치 3초 이내
- 4대 규칙 준수율 85% 이상
- 하드 제약 위반 0건
- 커버리지 90% 이상 (v5 목표)

---

## M — Model

### 테이블 스키마

```sql
CREATE TABLE schedules (
    schedule_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id        VARCHAR(32) NOT NULL,
    plan_id         UUID NOT NULL,        -- Service 02의 plan_id
    status          VARCHAR(16),          -- 'draft'|'confirmed'|'locked'
    total_assigned  INTEGER,
    coverage_pct    FLOAT,
    generated_at    TIMESTAMPTZ DEFAULT NOW(),
    generated_by    VARCHAR(64)
);

CREATE TABLE assignments (
    assignment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id     UUID REFERENCES schedules(schedule_id),
    applicant_id    VARCHAR(32) NOT NULL,
    interviewer_id  VARCHAR(32) NOT NULL,
    day             VARCHAR(4) NOT NULL,  -- 월|화|수|목|금
    hour            VARCHAR(8) NOT NULL,  -- 09시|10시|...
    lock_level      VARCHAR(16) DEFAULT 'DRAFT',  -- DRAFT|CONFIRMED|LOCKED
    reason_tags     TEXT[],
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_assign_schedule ON assignments(schedule_id);
CREATE UNIQUE INDEX idx_assign_time_iv ON assignments(schedule_id, interviewer_id, day, hour);

CREATE TABLE rule_compliance (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    schedule_id     UUID REFERENCES schedules(schedule_id),
    rule_name       VARCHAR(64),
    score           FLOAT,                -- 0-100
    details         JSONB,
    measured_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE interviewers (
    interviewer_id  VARCHAR(32) PRIMARY KEY,
    name            VARCHAR(64),
    team            VARCHAR(64) NOT NULL,
    max_daily       INTEGER DEFAULT 6,
    priority        INTEGER DEFAULT 2,    -- 1=리더, 2=실무
    email           VARCHAR(255),
    availability    JSONB                 -- {"월":["09시","10시"],...}
);
```

---

## A — API

### 시간표 생성
```
POST /api/v1/schedules/generate
body:
{
  "round_id": "R2026-Q3-01",
  "plan_id": "...",
  "algorithm": "v4_hierarchical",  # v1|v4|v5
  "constraints": {
    "grad_ratio_target": 0.30,
    "grad_ratio_tolerance": 0.20,
    "max_daily_default": 6
  }
}

response 201:
{
  "data": {
    "schedule_id": "...",
    "total_assigned": 88,
    "coverage_pct": 100.0,
    "hard_violations": 0,
    "rule_compliance": {
      "rule1_grad_balance": 60.0,
      "rule2_team_conflict": 100.0,
      "rule3_vertical_group": 100.0,
      "rule4_first_slot": 100.0,
      "overall": 90.0
    }
  }
}
```

### 시간표 조회
```
GET /api/v1/schedules/{schedule_id}
GET /api/v1/schedules/{schedule_id}/heatmap   # 요일×시간 히트맵 JSON
GET /api/v1/schedules/{schedule_id}/by-team   # 팀별 그룹핑
```

### 규칙 준수율 조회
```
GET /api/v1/schedules/{schedule_id}/rules

response 200:
{
  "data": {
    "rule1_grad_balance": {"score":60, "detail":{"월":0.45,"금":0.00}},
    ...
  }
}
```

### 하드 제약 검증
```
POST /api/v1/schedules/{schedule_id}/validate

response 200:
{"data": {"hard_violations": [], "soft_penalty": 0}}
```

### 락 단계 상승
```
POST /api/v1/schedules/{schedule_id}/lock
body: {"lock_level": "CONFIRMED", "applicant_ids": ["3339449", ...]}
```

### 면접관 관리
```
GET  /api/v1/interviewers?team=AI솔루션팀
POST /api/v1/interviewers          # 신규 등록
PUT  /api/v1/interviewers/{id}     # 가용성 업데이트
```

---

## D — Design

### 이벤트 발행
- `SCHEDULE_GENERATED`: 배치 완료
- `SCHEDULE_LOCKED`: 락 단계 상승
- `RULE_VIOLATED`: 하드 위반 발생

### 구독 이벤트
- `RESPONSE_RECEIVED`: 면접관 가용성 확정 → 스케줄 생성 트리거 가능
- `DISTRIBUTION_APPROVED`: 지원자 명단 확정

### 알고리즘 세 가지

**v1 (면접관 우선, 커버리지 100%)**
- 지원자 우선순위 순으로 슬롯 배정
- 소프트 페널티 23점, 리더 90% 부하 문제

**v4 (2단계 계층적, 규칙 준수 90%)**
- Stage 1: 팀 × 요일 배정
- Stage 1b: 요일별 학사/대학원 쿼터
- Stage 2: 시간대 세부 최적화
- 커버리지 68%로 하락

**v5 (통합, 두 지표 모두 90% 목표)**
- Stage 1 완화: 팀별 요일을 2~3개 허용
- Stage 3 (신규): Fallback 배치로 미배정자 흡수
- 소프트 페널티 감수하며 커버리지 확보

### 4대 규칙 준수율 계산
```python
def rule_compliance(assignments, interviewers, applicants) -> dict:
    # rule1: 요일별 대학원 비율 편차
    # rule2: 팀 동시간 중복 개수
    # rule3: 팀별 요일 내 슬롯 간격
    # rule4: 09시/14시 소규모 조 여부
    return {
        "rule1_grad_balance": ...,
        "rule2_team_conflict": ...,
        "rule3_vertical_group": ...,
        "rule4_first_slot": ...,
        "overall": ...
    }
```

### 락 시스템
- `DRAFT`: 자유롭게 재편성 가능
- `CONFIRMED`: 면접관 안내 발송 완료, 재편성 시 페널티
- `LOCKED`: 지원자 안내 발송 완료, 재편성 절대 금지

### 프로젝트 구조
```
scheduler/
├── app/
│   ├── main.py
│   ├── api/{schedules.py, interviewers.py, rules.py}
│   ├── domain/{assignment.py, schedule.py, interviewer.py}
│   ├── services/
│   │   ├── algorithm_v1.py        # 면접관 우선
│   │   ├── algorithm_v4.py        # 2단계 계층적
│   │   ├── algorithm_v5.py        # 통합
│   │   ├── constraint_checker.py  # 하드/소프트 검증
│   │   ├── rule_evaluator.py      # 4대 규칙 스코어
│   │   └── lock_manager.py
│   ├── infrastructure/{db.py, event_bus.py, response_client.py}
│   └── events.py
```

### 테스트 요구사항
- `test_algorithm_v1`: 88명 100% 배정, 하드 위반 0
- `test_algorithm_v4`: 4대 규칙 90%, 세로 연속 100%
- `test_rule1_grad`: 월요일 대학원 45% 상황에서 편차 감지
- `test_lock_upgrade`: DRAFT → CONFIRMED → LOCKED 순만 가능
- `test_hard_violation_zero`: 어떤 알고리즘도 하드 위반 발생 안 시킴

### 완료 판정 체크리스트
- [ ] `POST /schedules/generate` 3초 이내 응답
- [ ] `algorithm=v4` 실행 시 rule_compliance.overall ≥ 85
- [ ] `algorithm=v5` 실행 시 coverage_pct ≥ 90 AND rule_compliance.overall ≥ 85
- [ ] 락 레벨 강등 시도는 400 에러
- [ ] `SCHEDULE_LOCKED` 이벤트 발행 후 Service 05·06이 수신
