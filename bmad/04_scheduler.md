# 📦 Service 04 — Scheduler

> **필수 참조**: `00_SHARED_CONTRACT.md`
> **담당 단계**: 4️⃣ 면접 담당자 배치
> **포트**: 8004 · **DB 스키마**: `sched_db`

---

## B — Business Context

### 해결하는 문제
- 500명 규모 배치를 사람이 엑셀로 짜서 16~48시간 소요
- PPT 4대 규칙(날 분산·팀 중복·세로 연속·첫 타임)이 서로 상충
- 리더 과부하, 학사/대학원 날 편중 등 품질 문제

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
    day             VARCHAR(8) NOT NULL,  -- 1일차|2일차|…|5일차 (요일이 아니다)
    hour            VARCHAR(8) NOT NULL,  -- 1타임|2타임|... (자리 번호)
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
    max_daily       INTEGER DEFAULT 8,    -- 하루 칸 수(HOURS)와 같다
    priority        INTEGER DEFAULT 2,    -- 1=리더, 2=실무
    email           VARCHAR(255),
    availability    JSONB                 -- {"1일차":["1타임","2타임"],...}
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
    "max_daily_default": 8,
    "ignore_availability": false   # 켜면 담당자 가능 시간을 지키지 않고 자리부터 채운다
  },
  "pairs_by_team": {               # 부서가 확정한 짝 {팀: {면접자: 담당자}}
    "배터리기술팀": {"A001": "IV201"}
  },
  "seats_by_team": {               # 부서가 자기 시간표에서 잡아 둔 자리
    "배터리기술팀": {"A001": {"day": 1, "slot": 0}}
  }
}
```

`seats_by_team` 은 **몇 일차 · 몇 번째 칸**이다. 부서 화면은 실제 달력을 모르고
하루 몇 칸씩 끊어 세기만 하며, 그 날을 언제로 잡을지는 인사팀이 정한다(Stage 1).
안 주면 예전처럼 날 · 시각을 처음부터 새로 짠다.

**팀별 면접일을 고르는 기준** — `plan_team_days(sizes, days_per_team)` 은
**어느 팀에나 1일차부터** `days_per_team` 일을 준다. 인원 수도, 날별 부하도
보지 않는다. 한때는 큰 팀부터 한산한 날을 골라 가게 했는데, 그러면 뒤에 남은
팀은 앞날이 이미 차 있어 3일차에야 첫 면접을 보게 됐다 — "우리 팀은 왜 첫날
면접이 없나" 가 거기서 나왔다. 날을 비켜 준다고 얻는 것이 없다: 같은 팀이 같은
시각에 두 명을 볼 수 없다는 규칙2는 (팀, 날, 칸) 단위라 팀끼리는 애초에 안
부딪히고, 담당자는 자기 팀 면접만 본다.

담당자가 **어느 날에 나올 수 있는지는 보지 않는다.** 우리 모델에 담당자 가능
날이라는 것이 없다 — 가능 시간은 앞타임 · 뒤타임 · 모든타임 세 덩어리뿐이고,
그 덩어리는 어느 날에나 똑같이 적용된다. 저장 형식이 `{날: [칸]}` 이라
날이 딸려 들어가지만 `normalize_availability()` 가 읽는 순간 지운다. 한때 그
부산물을 사람의 뜻인 양 읽어 "모든 시간이 된다고 했는데 왜 빈 자리가 없나" 가
나왔다. 회귀 시험은 `services/04-scheduler/tests/test_team_days.py`.

```
response 201:
{
  "data": {
    "schedule_id": "...",
    "total_assigned": 88,
    "coverage_pct": 100.0,
    "hard_violations": 0,
    "off_band_count": 0,           # 담당자 사정과 어긋난 자리 (일정 무시로 만들었을 때만)
    "off_band": [],                # 위 자리의 담당자·면접자·날·칸 — 개별 조율용
    "dept_seats": 88,              # 부서가 보낸 자리 수
    "dept_seats_kept": 84,         # 그대로 지킨 자리 수
    "dept_seats_moved": {          # 옮긴 사람과 그 까닭 (SEAT_MOVED_*)
      "A031": "SEAT_MOVED_BAND"
    },
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
GET /api/v1/schedules/{schedule_id}/heatmap   # 날×시간 히트맵 JSON
GET /api/v1/schedules/{schedule_id}/by-team   # 팀별 그룹핑
```

### 규칙 준수율 조회
```
GET /api/v1/schedules/{schedule_id}/rules

response 200:
{
  "data": {
    "rule1_grad_balance": {"score":60, "detail":{"1일차":0.45,"5일차":0.00}},
    ...
  }
}
```

### 하드 제약 검증
```
POST /api/v1/schedules/{schedule_id}/validate

response 200:
{"data": {"hard_violations": [], "soft_penalty": 0, "off_band": []}}
```

`ignore_availability` 를 켜고 만든 시간표는 가용 시간대 밖 배정을 하드 위반으로
세지 않는다 — 인사가 자리를 채우려고 일부러 고른 것이기 때문이다. 대신 그
자리들이 `off_band` 로 나온다(누구와 다시 이야기해야 하는지). 켰다는 사실은
`rule_compliance` 의 `overall` 행 JSON 에 남으므로 나중에 다시 검증해도 같은
잣대를 쓴다.

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
PUT  /api/v1/interviewers/bands    # 가능 시간(앞타임 · 뒤타임) 일괄 저장
body: {"bands": {"IV101": "앞타임", "IV102": "뒤타임"}, "actor": "console"}
```

### 가능 시간 — 앞타임 · 뒤타임 (겹친다)

담당자에게 칸을 하나씩 고르게 하기는 번거로워 두 덩어리로만 받는다.

| 표기 | 뜻 |
|------|-----|
| `앞타임` | 아침부터 **14시까지** 있어 준다 |
| `뒤타임` | **12시부터** 와서 끝까지 있어 준다 |
| `모든타임` | 하루 종일 |
| `어려움` | 이번 회차는 못 들어간다 |

**날은 묻지 않는다.** 고른 덩어리는 면접 기간 어느 날에나 그대로 적용된다.
예전 이름이 `둘 다` 였는데 "날도 둘 다인가" 로 읽히는 일이 있어 `모든타임`
으로 바꿨다 — 저장된 자료의 옛 이름은 `LEGACY_BANDS` 가 받아 준다.

두 덩어리는 **12시 ~ 14시에서 일부러 겹친다**. 예전처럼 오전 · 오후로 갈라
받으면 정오에 걸치는 칸(기본 조건에서 11:55~12:25)을 어느 쪽도 맡을 수 없어
점심때가 통째로 빈다. 겹쳐 두면 `앞타임 ∪ 뒤타임 = 전체 칸` 이라 그 구멍이
생기지 않는다.

- 어느 칸이 어느 덩어리인지는 **면접 진행 조건**(시작 시각 · 한 사람당 분 ·
  쉬는 시간)이 정한다. 시각을 코드에 박아 두지 않는다.
- DB 에는 덩어리 이름이 아니라 `availability` (칸 목록)만 남는다. 이름은
  `band_of()` 로 되읽는다 — 그래서 표기가 바뀌어도 마이그레이션이 없다.
- 기본 조건(09:00 · 30분 · 5분)에서는 마지막 칸이 13:35 에 끝나므로 `앞타임`
  이 하루 전체를 덮는다. 이때 되읽으면 `모든타임` 으로 나오는 것이 맞다.
- 예전 표기(`오전만` · `오후만` · `오전·오후`)로 들어와도 받아 준다 — 현업
  엑셀에 그대로 남아 있다.

### 가용성이 어디서 오는가 — 목 데이터가 마스터를 지우면 안 된다

`load_interviewers()` 는 회차에 선별된 담당자마다 **면접관 마스터의 가용성**과
**03 회신**을 교집합으로 합친다. 둘 다 사람이 적어 낸 값이라 좁은 쪽을 따르는
것이 맞다.

여기에 목 데이터가 끼면 안 된다. `AvailabilitySource.fetch()` 는 03 이 죽어
있거나 회신이 0건이면 `USE_MOCK=true` 일 때 목 면접관으로 폴백하는데, 그 값을
교집합에 넣으면 **지어낸 시간과 실제 시간이 겹치는 칸만** 남는다. 목 IV101 은
7 · 8타임만 되는 사람이라, 마스터에 `3~7타임` 이라고 적어 낸 분이 스케줄러
안에서는 `7타임` 한 칸으로 쪼그라들었다. 그러면 부서 화면이 보고 보낸 자리가
절반 넘게 "담당자 시간 밖" 이 되어 도로 옮겨진다 — 부서가 짠 시간표와 최종
시간표가 서로 다른 물건이 되는 경로였다.

그래서 교집합을 잡는 자리에서는 `fetch(round_id, allow_mock=False)` 로 부른다.
회신이 없으면 빈 명단을 받고 마스터 가용성을 그대로 쓴다. 목 폴백이 남아 있는
곳은 **겹쳐 볼 마스터가 아예 없는** 경로(면접관 DB 가 비어 있을 때)뿐이다.

> 이 버그는 마스터와 목이 **한 칸도 안 겹칠 때만** 마스터로 되돌리는 규칙에
> 오래 가려져 있었다. 회귀 시험은 일부러 한 칸 겹치게 잡는다
> (`test_master_hours_survive_when_nobody_answered`).

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
- Stage 1: 팀 × 날 배정
- Stage 1b: 날별 학사/대학원 쿼터
- Stage 2: 시간대 세부 최적화
- 커버리지 68%로 하락

**v5 (통합, 두 지표 모두 90% 목표)**
- Stage 1 완화: 팀별 면접일을 2~3일 허용
- Stage 3 (신규): Fallback 배치로 미배정자 흡수
- 소프트 페널티 감수하며 커버리지 확보

**Stage 0 — 부서가 잡아 둔 자리 (`seats_by_team` 을 줬을 때만)**

Stage 1 로 팀별 면접일을 정한 뒤, Stage 1b 로 넘어가기 전에 먼저 돈다. 부서가
말한 n일차를 그 팀에 잡힌 날 중 n 번째로 옮겨 읽고 그 칸에 앉힌다. 여기서
앉은 사람은 `DEPT_SEAT` 사유가 붙고 뒤 단계는 건드리지 않는다.

못 앉히면 **억지로 밀어 넣지 않는다** — 부서 결정을 시간표가 뒤집지 않는다는
원칙(짝을 정해 보낸 사람에게 다른 담당자를 대신 세우지 않는 것과 같은 이유)은
그대로다. 대신 왜 못 앉혔는지를 보드에 적어 두고, 그 사람이 나중에 다른 칸에
앉을 때 그 까닭이 배정 사유로 따라간다.

| 사유 | 뜻 |
| --- | --- |
| `SEAT_MOVED_TAKEN` | 그 자리를 이미 다른 사람이 씀 |
| `SEAT_MOVED_BAND` | 담당자 가능 시간 밖 |
| `SEAT_MOVED_CAP` | 담당자 하루 한도가 참 |
| `SEAT_MOVED_BUSY` | 담당자가 그 시각에 다른 면접 중 |
| `SEAT_MOVED_OWNER` | 그 자리 담당자를 못 찾음 (남의 팀 · 명단에 없음) |
| `SEAT_MOVED_DAY` | 부서가 적은 일차가 이 팀 면접일 수보다 큼 |

### 시작할 때 도는 보정 — `rederive_bands`

가능 시간은 덩어리(앞타임 · 뒤타임 · 모든타임)로 받아 칸 목록으로 펼쳐 저장하고,
**덩어리 이름은 저장하지 않는다**. 그래서 덩어리 규칙을 바꿔도 이미 저장된 칸
목록은 옛 규칙 그대로 남는다. 오전 · 오후로 반씩 자르던 시절 자료는 정오
언저리 칸이 비어 있어, 그 팀 담당자 전원이 그 칸을 못 맡는다 — 시간표 한가운데
빈 칸의 원인이었다. 서비스가 뜰 때 한 번 다시 펼친다. 하루 한도(`max_daily`)는
사람이 손으로 낮춰 둔 값일 수 있으므로 건드리지 않는다.

### 4대 규칙 준수율 계산
```python
def rule_compliance(assignments, interviewers, applicants) -> dict:
    # rule1: 날별 대학원 비율 편차
    # rule2: 팀 동시간 중복 개수
    # rule3: 팀별 하루 안 슬롯 간격
    # rule4: 첫 타임(1타임) 소규모 조 여부
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
- `test_rule1_grad`: 1일차 대학원 45% 상황에서 편차 감지
- `test_lock_upgrade`: DRAFT → CONFIRMED → LOCKED 순만 가능
- `test_hard_violation_zero`: 어떤 알고리즘도 하드 위반 발생 안 시킴

### 완료 판정 체크리스트
- [ ] `POST /schedules/generate` 3초 이내 응답
- [ ] `algorithm=v4` 실행 시 rule_compliance.overall ≥ 85
- [ ] `algorithm=v5` 실행 시 coverage_pct ≥ 90 AND rule_compliance.overall ≥ 85
- [ ] 락 레벨 강등 시도는 400 에러
- [ ] `SCHEDULE_LOCKED` 이벤트 발행 후 Service 05·06이 수신
