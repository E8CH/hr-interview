# Service 03 — Response Collector

면접위원에게 초대를 발송하고, 구조화 웹폼(앞타임 · 뒤타임 · 모든타임 중 하나)으로 가능 시간을 수집하며,
3단계 자동 리마인더와 조직별 응답 패턴 학습을 담당한다.

- 포트: **8003**
- DB 스키마: **resp_db** (PoC 모드에서는 SQLite 파일)
- BMAD 명세: `../../bmad/03_response_collector.md`
- 공통 계약: `../../bmad/00_SHARED_CONTRACT.md` · `../../shared/contracts/` (**읽기 전용**)

---

## 1. 실행 (Docker 불필요)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt

cp .env.example .env            # 이미 있으면 생략
uvicorn app.main:app --port 8003 --reload
```

기동 후:

| URL | 용도 |
| --- | --- |
| `http://localhost:8003/docs` | Swagger UI |
| `http://localhost:8003/healthz` | 헬스체크 |
| `http://localhost:8003/metrics` | Prometheus 텍스트 지표 |
| `http://localhost:8003/form/{token}` | 면접위원용 응답 폼 |

### PoC 모드 환경변수 (`.env`)

```ini
DATABASE_URL=sqlite:///./resp_db.sqlite   # SQLite 파일
REDIS_URL=fakeredis://                    # 인메모리 이벤트 버스
USE_MOCK=true                             # 타 서비스(알림/명단) mock
STORAGE_DIR=./storage                     # 로컬 파일 저장 (mock outbox)
FORM_BASE_URL=http://localhost:8003       # 폼 링크 생성용
ENABLE_SCHEDULER=true                     # APScheduler on/off
REMINDER_POLL_MINUTES=30                  # 리마인더 폴링 주기 (명세값)
REMINDER_POLL_SECONDS=0                   # >0 이면 초 단위로 오버라이드 (데모/검증용)
```

통합 모드로 넘어갈 때는 `DATABASE_URL` 을 PostgreSQL, `REDIS_URL` 을 실제 Redis,
`USE_MOCK=false` 로 바꾸면 코드 수정 없이 전환된다 (`.env.example` 참고).

---

## 2. 테스트

```bash
pytest                       # pytest.ini 가 커버리지 게이트(70%)를 포함
```

현재 상태: **170 tests passed · coverage 96.38%** (요구치 70% 이상).
테스트는 임시 디렉터리 DB + fakeredis 로 완전히 격리되며 스케줄러는 꺼진 채 돈다
(`tests/conftest.py` 가 `app.config` import 전에 환경변수를 세팅).

---

## 3. 담당 기능

### 3.1 초대 발송
`DISTRIBUTION_APPROVED` 구독 또는 `POST /api/v1/requests` 로 라운드를 시작하면
초대자별 고유 토큰(`secrets.token_urlsafe(32)`)을 발급하고 폼 링크가 담긴 초대를 발송한 뒤
`REQUEST_SENT` 를 발행한다.

### 3.2 구조화 웹폼
`app/templates/form.html` — **가능 시간 덩어리 셋 중 하나**(앞타임 · 뒤타임 · 모든타임)를 고르는 화면.
날은 묻지 않는다 — 담당자 가능 날이라는 개념이 우리 모델에 없다. 고른 덩어리는
브라우저가 모든 날 × 그 칸으로 펼쳐 보낸다.
CSS·JS 전부 인라인이라 외부 리소스 요청이 0건이고, 서버 렌더 응답만으로 화면이 완성된다.
이미 제출했거나 마감된 요청은 잠금 상태로 렌더된다.

### 3.3 응답 검증
`app/services/validator.py` — 명세의 `validate_form_response` 를 그대로 구현하고
날/시간 리터럴 검사, 중복 슬롯, `max_daily` 범위, 백업 연락처 형식을 추가로 본다.
날 이름이 요일('월')로 온 옛 폼 응답은 `day_name()` 이 1일차로 맞춰 받아 준다.
실패 시 `VALIDATION_FAILED` 와 함께 위반 목록을 반환한다.

### 3.4 3단계 리마인더
```
Level 1 — 발송 후 24h — 정중      — 상급자 CC 없음
Level 2 — 발송 후 48h — 마감강조   — 상급자 CC 없음
Level 3 — 발송 후 68h — 최종알림   — 상급자 CC (+ NON_RESPONDER_ESCALATED)
```
APScheduler `BackgroundScheduler` 가 30분 주기로 폴링하며 `misfire_grace_time=300` 으로
±5분 정확도를 지킨다. 판정은 순수 함수 `should_send_reminder` 가 하고,
스케줄러가 멈췄다 재개돼도 밀린 레벨을 연속 발송하지 않고 **가장 높은 레벨 1건으로 접는다**.
회신자·마감된 요청은 후보에서 제외된다.

### 3.5 조직별 응답 패턴 학습
`app/services/pattern_learner.py` — Welford 온라인 알고리즘으로 조직별 평균/표준편차를
증분 갱신한다(재계산 없음). 평균 응답 시간이 **40시간 초과**면 지연 조직으로 예측한다.

---

## 4. API

| Method | Path | 설명 |
| --- | --- | --- |
| POST | `/api/v1/requests` | 초대 요청 생성·발송 (201) |
| GET | `/api/v1/requests/{request_id}` | 요청 상세 · 회신 현황 |
| POST | `/api/v1/requests/{request_id}/close` | 요청 마감 |
| GET | `/form/{token}` | 응답 폼 (HTML) |
| POST | `/form/{token}/submit` | 응답 제출 |
| GET | `/api/v1/responses/{round_id}` | 라운드 회신 집계 (`team`/`org`/`include_payload` 필터) |
| GET | `/api/v1/patterns/organizations` | 조직별 응답 패턴 (느린 순) |
| GET | `/api/v1/patterns/organizations/{org}` | 특정 조직 패턴 · 지연 예측 |
| POST | `/api/v1/reminders/trigger` | 리마인더 수동 발송 |
| POST | `/api/v1/reminders/run-cycle` | 리마인더 사이클 즉시 실행 (스케줄러와 동일 경로) |
| GET | `/api/v1/reminders/rules` | REMINDER_RULES 조회 |
| GET | `/api/v1/reminders/schedule/{invitee_id}` | 해당 초대자의 다음 리마인더 판정 |
| GET | `/healthz` · `/metrics` · `/` | 운영 엔드포인트 |

응답은 공통 규약을 따른다 — 성공 `{"data": ..., "error": null}`,
실패 `{"data": null, "error": {"code": "UPPER_SNAKE", "message": "..."}}`.

---

## 5. 이벤트 계약

**발행**

| 이벤트 | 시점 |
| --- | --- |
| `REQUEST_SENT` | 초대 발송 완료 |
| `RESPONSE_RECEIVED` | 응답 검증 통과·저장 |
| `REMINDER_SENT` | 리마인더 발송 (레벨 포함) |
| `NON_RESPONDER_ESCALATED` | Level 3 도달 — 상급자 CC |

**구독**

| 이벤트 | 처리 |
| --- | --- |
| `DISTRIBUTION_APPROVED` | 초대 요청 자동 생성 (기본 마감 72h). `(plan_id, round_id)` 기준 멱등 |

봉투(envelope)는 `shared/contracts/events.py` 의 `EventEnvelope` 를 그대로 쓴다.
`NON_RESPONDER_ESCALATED` 의 페이로드 모델은 공통 계약에 없어서 **`shared/` 를 건드리지 않고**
`app/events.py` 에 `NonResponderEscalatedPayload` 로 로컬 정의했다 (봉투 형식은 동일).

---

## 6. Before/After 시뮬레이션

```bash
python scripts/simulate.py                 # 기본 200명 · 고정 시드
python scripts/simulate.py --n 500 --json
```

이산 사건 시뮬레이션(로그정규 지연 + 무응답 인구 + 리마인더 전환).
기본 시드 결과:

| 지표 | Before | After | 개선 |
| --- | --- | --- | --- |
| 평균 응답 시간 | 29.88h | 12.3h | **58.8% 단축** |
| 미회신율 | 16.0% | 7.5% | **8.5%p 감소** |

명세 목표(30h → 12h, 15% → 7%)를 충족하며, 다른 시드 5종에서도 동일 경향을 확인했다.

---

## 7. 완료 판정 체크리스트 — 실측 결과

라이브 서버를 띄우고 `scripts/smoke.py` 로 E2E 실측한다.

```bash
# 터미널 1 — 리마인더 자동 발송을 10초 주기로 관찰
REMINDER_POLL_SECONDS=10 uvicorn app.main:app --port 8003

# 터미널 2
python scripts/smoke.py --wait-reminder 14
```

결과: **12/12 통과**

| 명세 체크리스트 | 결과 |
| --- | --- |
| 폼 페이지 로딩 1초 이내 | ✅ 최대 **7ms** (외부 리소스 0건) |
| 응답 제출 500ms 이내 | ✅ 최대 **13ms** |
| 리마인더 자동 발송 로그 확인 | ✅ `scheduler_started` → `reminder_cycle_done(sent=2)` → `reminder_sent` / `non_responder_escalated` |
| RESPONSE_RECEIVED 이벤트 발행 | ✅ 제출 3건 → 카운터 +3 |
| Before/After 시뮬레이션 | ✅ 29.88h → 12.3h · 16.0% → 7.5% |

부가 확인: 초대 5명 등록·토큰 유일성, 회신 집계 3/5, 조직 패턴 학습, Level 3 상급자 CC
에스컬레이션 2건, **회신자에게는 리마인더 미발송**, mock outbox 발송 로그(초대 5 · 리마인더 2).

---

## 8. 구조

```
app/
  main.py                 FastAPI 조립 · lifespan · /healthz · /metrics
  config.py               .env 로드 (PoC 기본값)
  events.py               봉투 생성 · 로컬 페이로드 확장
  subscribers.py          DISTRIBUTION_APPROVED 핸들러
  timeutil.py             naive-UTC 시간 규약
  domain/                 SQLAlchemy 모델 (Request/Invitee/Response/Reminder/OrgPattern)
  services/
    validator.py          응답 스키마 검증
    reminder_engine.py    REMINDER_RULES · 순수 판정 로직
    reminder_service.py   발송 · 이벤트 · 에스컬레이션
    pattern_learner.py    Welford 증분 통계
    request_service.py    초대 요청 생성/마감
    response_service.py   응답 저장
    notification_client.py / roster_client.py / messages.py
    simulation.py         Before/After 시뮬레이션 모델
  infrastructure/
    db.py                 엔진 · 세션 · init_db
    event_bus.py          fakeredis Pub/Sub 래퍼
    scheduler.py          APScheduler 리마인더 잡
  api/                    라우터 + 공통 에러 핸들러
  templates/form.html     인라인 CSS/JS 슬롯 그리드
scripts/  simulate.py · smoke.py
tests/    170 tests
```

시간은 내부적으로 **naive UTC** 로 저장한다(SQLite 가 tz 를 버림). API 경계에서만 tz-aware 로 변환.
