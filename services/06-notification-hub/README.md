# Service 06 — Notification Hub

통합 알림 발송 허브. 모든 서비스는 이메일/Slack/SMS 를 직접 다루지 않고 이곳으로 위임한다.

- 포트: **8006**
- DB 스키마: **notif_db**
- BMAD 명세: `../../bmad/06_notification_hub.md`
- 공통 계약: `../../bmad/00_SHARED_CONTRACT.md`

## 빠른 시작 (Docker 불필요)

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8006 --reload
# → http://localhost:8006/docs
```

기동 시 자동으로 SQLite 테이블 생성 + 기본 템플릿 10종 · 채널 4종 seed 가 삽입된다.

```bash
# 테스트 + 커버리지
pytest --cov=app --cov-report=term-missing
```

## PoC 모드

`.env` 기준 동작:

| 항목 | PoC 값 | 비고 |
|---|---|---|
| DB | `sqlite:///./notif_db.sqlite` | 파일 기반, 자동 생성 |
| 이벤트 버스 | `fakeredis://` | 인메모리 Pub/Sub |
| 파일 저장 | `./storage/` | 발송 산출물 |
| 발송 | `USE_MOCK=true` | 실제 전송 없이 `storage/outbox/<채널>/*.txt` 로 기록 |
| SMS | stub | 항상 파일로만 기록 |

`USE_MOCK=false` 로 두면 SMTP / SendGrid API / Slack Webhook 을 실제 호출한다.
환경변수 전체는 `.env.example` 참조.

## API

모든 경로는 `/api/v1/notify` 프리픽스, 응답은 `{"data": ..., "error": null}` 규약.

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/send` | 단건 발송 (202, `queued`) |
| POST | `/broadcast` | 다중 발송 — 수신자 1명당 알림 1건 생성 |
| GET | `/history` | 이력 조회 (`correlation_id`, `round_id`, `status`, `template_id` 필터) |
| GET | `/history/{recipient}` | 수신자별 이력 |
| GET | `/dead-letters` | dead letter 큐 조회 |
| POST | `/process` | 큐 수동 처리 (워커 없이 운영/디버깅) |
| GET | `/track/open/{notification_id}.png` | 1x1 투명 PNG · 열람 기록 |
| GET | `/templates`, `/templates/{id}` | 템플릿 조회 (변수 목록 포함) |
| PUT | `/templates/{id}` | 템플릿 등록/수정 (Jinja2 문법 검증) |
| POST | `/templates/{id}/preview` | 저장 없이 렌더링 미리보기 |
| GET | `/channels` | 채널 목록 (자격증명 마스킹) |
| POST | `/channels` | 채널 등록 |
| PUT | `/channels/{id}/toggle` | 채널 on/off |
| GET | `/events` | 발행·구독 이벤트 카탈로그 |
| POST | `/events/inbound` | 외부 이벤트 수신 (Redis 없이 HTTP 로 주입) |

공통: `GET /healthz`, `GET /metrics` (Prometheus 텍스트).

### 인증

공통 계약 §1 대로 **Bearer JWT (HS256, 만료 8h)** 를 지원한다. PoC 기본값은 `AUTH_ENABLED=false`
(서비스 간 호출을 토큰 없이 열어 둠). `AUTH_ENABLED=true` 로 올리면 `/api/v1/notify/**` 전체가
`Authorization: Bearer <token>` 을 요구하고, 없거나 만료·위변조 시 `401 UNAUTHORIZED` 를 반환한다.

`/healthz` · `/metrics` · 트래킹 픽셀(`/track/open/*.png`)은 **항상 공개** 다 — 픽셀은 메일
클라이언트가 호출하므로 토큰을 붙일 수 없다.

```python
from app.security import create_token
create_token("service-03")   # 발급
```

### 예시

```bash
curl -X POST http://localhost:8006/api/v1/notify/send \
  -H 'Content-Type: application/json' \
  -d '{"template_id":"reminder_l1","channel":"email","recipient":"iv1@lge.com",
       "cc":["backup@lge.com"],
       "context":{"name":"이지훈","deadline":"2026-07-31 18:00",
                  "form_link":"https://hr.lge.com/form/abc123"},
       "correlation_id":"R2026-Q3-01/invitee-abc"}'
```

## 발송 파이프라인

```
요청 → 템플릿 렌더링(Jinja2) → notifications(queued) 저장 → 202 응답
     → 백그라운드 워커 pull → 채널 어댑터 발송
     → 성공: sent + NOTIFICATION_SENT
     → 실패: 재시도(0s → 30s → 5m) → 3회 초과 시 failed + dead_letters + NOTIFICATION_FAILED
```

재시도 백오프는 `notifications.next_attempt_at` 으로 스케줄링한다.
`RETRY_DELAYS` 환경변수로 조정 가능 (테스트에서는 `0,0,0`).

## 채널 어댑터

`app/services/channels/` 아래에 격리. 자격증명은 어댑터와 `channels.config` 안에만 존재하며,
API 응답에서는 마스킹된다.

| 어댑터 | 파일 | channel_type |
|---|---|---|
| SMTP | `email_smtp.py` | email |
| SendGrid | `email_sendgrid.py` | email |
| Slack Webhook | `slack_webhook.py` | slack |
| SMS (stub) | `sms_stub.py` | sms |

## 템플릿 10종 (seed)

`invite` · `reminder_l1` · `reminder_l2` · `reminder_l3` · `applicant_invite` ·
`applicant_change` · `applicant_defer` · `interviewer_confirm` ·
`hr_alert_integrity` · `hr_alert_repair`

Jinja2 `StrictUndefined` 로 렌더링하므로 **필수 변수가 빠지면 422 로 즉시 실패**한다
(조용히 빈 문자열로 치환되지 않음). 선택 변수는 `| default(...)` 로 정의되어 있다.

## 이벤트

**발행**: `NOTIFICATION_SENT` · `NOTIFICATION_FAILED` · `NOTIFICATION_OPENED`

**구독 → 자동 발송**:

| 이벤트 | 발송 템플릿 |
|---|---|
| `DISTRIBUTION_APPROVED` | `applicant_invite` (대량) |
| `REQUEST_SENT` | `invite` |
| `REMINDER_SENT` | `reminder_l{level}` |
| `NON_RESPONDER_ESCALATED` | `reminder_l3` (상급자 CC) |
| `SCHEDULE_LOCKED` | `interviewer_confirm` + `applicant_invite` |
| `REPAIR_EXECUTED` | `applicant_change` / `applicant_defer` (없으면 `hr_alert_repair`) |
| `PARTICIPANT_DEFERRED` | `applicant_defer` |
| `INTEGRITY_VIOLATED` | `hr_alert_integrity` (Slack) |

수신자는 payload 의 `recipients` / `invitees` / `applicants` / `interviewers` /
`rebooked_recipients` / `deferred_recipients` 키에서 추출한다.
각 항목은 `{"email": "...", "context": {...}}` 또는 스칼라 필드를 그대로 둔 평평한 dict 를 허용한다.

### 계약 관련 메모

- `NOTIFICATION_OPENED` 는 `00_SHARED_CONTRACT.md` 카탈로그에 없는 **06 로컬 확장** 이벤트다
  (명세 06 이 요구). `EventEnvelope` 봉투 규격은 그대로 지킨다.
- `shared/contracts` 는 읽기 전용이므로 공통 계약 §3 지침대로 `app/contracts/` 에
  vendored 복사본을 유지한다. 원본은 수정하지 않는다.
- `NON_RESPONDER_ESCALATED` (03 → 06) 와 `PARTICIPANT_DEFERRED` (05 → 06) 는 명세 06 의
  구독 목록에는 빠져 있지만 `00_SHARED_CONTRACT.md` 이벤트 카탈로그가 06 을 구독자로
  명시하므로 함께 구현했다. 총 구독 이벤트는 **8종**.

## 마이그레이션 · Docker

PoC 는 기동 시 `create_all` 로 테이블을 만들지만, 공통 계약 §10 대로 Alembic 스크립트도 함께 둔다.

```bash
alembic upgrade head     # DATABASE_URL 환경변수를 그대로 사용
alembic downgrade base
```

`tests/test_migrations.py` 가 마이그레이션 결과와 `Base.metadata` 의 테이블·컬럼·PK·인덱스를
대조하므로 모델과 스크립트가 갈라지면 테스트가 깨진다.

PostgreSQL + Redis 구성을 검증하려면 `docker-compose up` (§11.4). 해커톤 기본 경로는 아니다.

## 스키마 매핑 (PostgreSQL → SQLite)

| 원본 | PoC |
|---|---|
| `UUID` | `VARCHAR(36)` (uuid4 문자열) |
| `TEXT[]` (cc) | JSON 배열 |
| `JSONB` (context, config) | JSON |
| `TIMESTAMPTZ` | `DateTime` (naive UTC) |

명세 스키마 외 내부 확장 컬럼: `notifications.next_attempt_at`, `notifications.round_id`,
그리고 dead letter 큐 테이블 `dead_letters`.

## 완료 판정 체크리스트

- [x] SMTP · Slack · SMS 3개 채널 어댑터 구현 (+ SendGrid, SMS 는 stub)
- [x] 재시도 3회 후 dead letter
- [x] `NOTIFICATION_SENT` 이벤트 발행 확인
- [x] Pixel 열람 추적 동작
- [x] 10개 기본 템플릿 seed 데이터 삽입
- [x] pytest 커버리지 70% 이상 (현재 **92%**, 129 tests)
- [x] uvicorn 로컬 실행 (Docker 불필요)
- [x] 발송 요청 API 200ms 이내 (`tests/test_performance.py`)
- [x] JWT 인증 (§1) · Alembic 마이그레이션 (§10) · docker-compose (§11.4)
