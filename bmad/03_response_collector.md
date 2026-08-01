# 📦 Service 03 — Response Collector

> **필수 참조**: `00_SHARED_CONTRACT.md`
> **담당 단계**: 3️⃣ 면접 담당자 회신
> **포트**: 8003 · **DB 스키마**: `resp_db`

---

## B — Business Context

### 해결하는 문제
- 자유 텍스트 이메일 회신 → 취합 혼선, 파싱 불가
- 회신 대기가 전체 프로세스의 최대 병목 (16~48시간 중 절반 이상)
- 리마인더는 담당자 수동 발송, 조직별 응답 패턴 축적 안 됨

### 비즈니스 가치
- 구조화 웹폼으로 회신 소요시간 60% 단축 (30h → 12h, 시뮬레이션 검증)
- 24h/48h/68h 3단계 자동 리마인더 → 미회신율 15% → 7%
- 조직별 평균 응답 시간 학습 → v2 대시보드의 “협업 온도계” 데이터 공급

### 성공 기준
- 웹폼 로딩 1초 이내 · 제출 500ms 이내
- 리마인더 스케줄 정확도 ±5분
- 전체 회신 완료율 95% 이상

---

## M — Model

### 테이블 스키마

```sql
CREATE TABLE requests (
    request_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id        VARCHAR(32) NOT NULL,
    plan_id         UUID NOT NULL,        -- Service 02의 plan_id
    team_name       VARCHAR(64),
    sent_at         TIMESTAMPTZ,
    deadline        TIMESTAMPTZ NOT NULL,
    status          VARCHAR(16) DEFAULT 'active'  -- 'active'|'closed'
);

CREATE TABLE invitees (
    invitee_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id      UUID REFERENCES requests(request_id),
    name            VARCHAR(64) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    team            VARCHAR(64) NOT NULL,
    org             VARCHAR(64),
    dept_leader_email VARCHAR(255),       -- Level 3 CC
    token           VARCHAR(64) UNIQUE NOT NULL,  -- 폼 접근 토큰
    first_opened_at TIMESTAMPTZ,
    last_reminder_level INTEGER DEFAULT 0
);
CREATE INDEX idx_invitees_token ON invitees(token);

CREATE TABLE responses (
    response_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invitee_id      UUID REFERENCES invitees(invitee_id) UNIQUE,
    submitted_at    TIMESTAMPTZ NOT NULL,
    payload         JSONB NOT NULL,       -- {job_role, available_slots, max_daily, backup, notes}
    validated       BOOLEAN DEFAULT FALSE
);

CREATE TABLE reminders (
    reminder_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invitee_id      UUID REFERENCES invitees(invitee_id),
    level           INTEGER NOT NULL,     -- 1|2|3
    sent_at         TIMESTAMPTZ DEFAULT NOW(),
    channel         VARCHAR(16),          -- 'email'|'slack'|'sms'
    cc_supervisor   BOOLEAN DEFAULT FALSE
);

CREATE TABLE org_patterns (
    org             VARCHAR(64) PRIMARY KEY,
    mean_hours      FLOAT,
    std_hours       FLOAT,
    sample_count    INTEGER,
    predicted_slow  BOOLEAN,
    updated_at      TIMESTAMPTZ
);
```

### 폼 응답 스키마 (payload JSONB)
```json
{
  "job_role": "직무다",
  "available_slots": [
    {"day": "2일차", "hour": "10시"},
    {"day": "2일차", "hour": "11시"}
  ],
  "max_daily": 6,
  "backup_contact": "backup@company.com",
  "notes": "8/5 오전 학회 발표"
}
```

---

## A — API

### 요청 발송
```
POST /api/v1/requests
body:
{
  "round_id": "R2026-Q3-01",
  "plan_id": "...",
  "deadline": "2026-07-31T18:00:00Z",
  "invitees": [
    {"name":"이지훈","email":"iv1@lge.com","team":"AI솔루션팀",
     "org":"제1기술원","dept_leader_email":"lead@lge.com"}
  ]
}

response 201:
{"data": {"request_id":"...", "sent_count": 15}}
```

### 응답 폼 (공개 페이지)
```
GET  /form/{token}          # 폼 HTML 렌더링
POST /form/{token}/submit   # 응답 제출
body: {폼 응답 스키마}
```

### 회신 현황
```
GET /api/v1/responses/{round_id}?team=AI솔루션팀

response 200:
{
  "data": {
    "total": 15, "responded": 12, "pending": 3,
    "avg_response_hours": 8.4,
    "responses": [...]
  }
}
```

### 조직 응답 패턴
```
GET /api/v1/patterns/organizations

response 200:
{
  "data": [
    {"org":"제1기술원","mean_hours":6.0,"predicted_slow":false},
    {"org":"제3기술원","mean_hours":52.0,"predicted_slow":true}
  ]
}
```

### 리마인더 트리거 (수동)
```
POST /api/v1/reminders/trigger
body: {"invitee_id": "...", "level": 2}
```

---

## D — Design

### 이벤트 발행
- `REQUEST_SENT`: 초대 발송 완료
- `RESPONSE_RECEIVED`: 응답 제출 · 스키마 검증 통과
- `REMINDER_SENT`: 리마인더 발송
- `NON_RESPONDER_ESCALATED`: Level 3(상급자 CC) 도달

### 구독 이벤트
- `DISTRIBUTION_APPROVED`: 자동 요청 발송 트리거

### 3단계 리마인더 규칙
```python
REMINDER_RULES = [
    {"level":1, "hours_after_send":24, "tone":"정중",     "cc_supervisor":False},
    {"level":2, "hours_after_send":48, "tone":"마감강조", "cc_supervisor":False},
    {"level":3, "hours_after_send":68, "tone":"최종알림", "cc_supervisor":True},
]
```
- APScheduler로 30분 주기 폴링 → `should_send_reminder(now, invitee, response_received)`

### 폼 응답 스키마 검증
```python
def validate_form_response(payload) -> tuple[bool, str]:
    required = ["job_role", "available_slots"]
    for k in required:
        if k not in payload: return False, f"필수 누락: {k}"
    if not payload["available_slots"]: return False, "슬롯 없음"
    for s in payload["available_slots"]:
        if "day" not in s or "hour" not in s:
            return False, f"슬롯 형식 오류: {s}"
    return True, "OK"
```

### 조직 응답 패턴 학습
- 응답 제출 시마다 `org_patterns` 테이블 업데이트
- `predicted_slow = mean_hours > 40`

### 프로젝트 구조
```
response-collector/
├── app/
│   ├── main.py
│   ├── api/{requests.py, form.py, patterns.py}
│   ├── domain/{request.py, invitee.py, response.py}
│   ├── services/
│   │   ├── validator.py       # 폼 스키마 검증
│   │   ├── reminder_engine.py # 3단계 리마인더
│   │   ├── pattern_learner.py # 조직별 응답 패턴
│   │   └── notification_client.py  # Service 06 호출
│   ├── infrastructure/{db.py, event_bus.py, scheduler.py}
│   ├── templates/form.html    # 폼 UI
│   └── events.py
```

### 프론트 (폼)
- 서버 사이드 렌더링 (Jinja2)
- 가능 시간 = **덩어리 셋 중 하나**(앞타임 · 뒤타임 · 모든타임) 단일 선택.
  **날은 묻지 않는다** — 담당자 가능 날이라는 개념이 우리 모델에 없다.
  예전에는 날 5 × 하루 8칸 격자를 그렸는데, 거기 찍힌 날이 아무 뜻 없이
  배치에서 자리를 막았다.
- 고른 덩어리를 브라우저가 **모든 날 × 그 칸**으로 펼쳐 `available_slots`
  으로 보낸다. 저장 형식(`{day, hour}`)은 그대로다 — 04 가 읽을 때
  `normalize_availability()` 로 날을 지우므로 마이그레이션이 필요 없다.
- 제출 시 JSON payload로 POST

### 테스트 요구사항
- `test_validator`: 정상/불량 응답 판정
- `test_reminder_schedule`: 24h/48h/68h 정확한 시각 계산
- `test_should_send`: 이미 회신한 사람에겐 리마인더 안 감
- `test_pattern_learning`: 히스토리 200건 → 조직별 평균 계산

### 완료 판정 체크리스트
- [ ] 폼 페이지 로딩 1초 이내
- [ ] 응답 제출 500ms 이내
- [ ] 리마인더 자동 발송 로그 확인
- [ ] `RESPONSE_RECEIVED` 이벤트 발행 확인
- [ ] Before/After 시뮬레이션: 회신 소요 30h → 12h 재현
