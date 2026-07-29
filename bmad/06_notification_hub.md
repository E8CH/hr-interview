# 📦 Service 06 — Notification Hub

> **필수 참조**: `00_SHARED_CONTRACT.md`
> **담당 단계**: 공통 인프라 (전 단계)
> **포트**: 8006 · **DB 스키마**: `notif_db`

---

## B — Business Context

### 해결하는 문제
- 각 서비스마다 이메일 발송 로직 중복 구현
- 채널(이메일/Slack/SMS) 관리가 파편화
- 발송 이력이 흩어져 있어 감사 불가

### 비즈니스 가치
- 단일 진입점으로 모든 알림 관리
- 템플릿 중앙 관리로 톤·브랜딩 일관성
- 발송 실패 자동 재시도, 열람 추적
- 채널 자격증명 격리 (다른 서비스는 SMTP 정보 몰라도 됨)

### 성공 기준
- 발송 요청 API 200ms 이내
- 재시도 3회 후 실패 시 dead letter 큐로
- 발송 이력 100% 저장

---

## M — Model

### 테이블 스키마

```sql
CREATE TABLE templates (
    template_id     VARCHAR(64) PRIMARY KEY,   -- 'invite'|'reminder_l1'|...
    channel         VARCHAR(16),                -- 'email'|'sms'|'slack'
    subject         TEXT,
    body            TEXT NOT NULL,              -- Jinja2 템플릿
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE notifications (
    notification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_id     VARCHAR(64),
    channel         VARCHAR(16),
    recipient       VARCHAR(255) NOT NULL,
    cc              TEXT[],
    context         JSONB,                      -- 템플릿 렌더링 변수
    subject         TEXT,
    body            TEXT,
    status          VARCHAR(16),                -- 'queued'|'sent'|'failed'|'opened'
    attempt_count   INTEGER DEFAULT 0,
    sent_at         TIMESTAMPTZ,
    opened_at       TIMESTAMPTZ,
    error_message   TEXT,
    correlation_id  VARCHAR(64),                -- 회차/이벤트 추적
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_notif_status ON notifications(status, created_at);
CREATE INDEX idx_notif_correlation ON notifications(correlation_id);

CREATE TABLE channels (
    channel_id      VARCHAR(64) PRIMARY KEY,    -- 'sendgrid'|'gmail_smtp'|'slack_hr'|...
    channel_type    VARCHAR(16),                -- 'email'|'slack'|'sms'
    config          JSONB,                       -- 자격증명, webhook URL 등
    enabled         BOOLEAN DEFAULT TRUE
);
```

### 기본 템플릿 세트 (초기 삽입)
- `invite`: 면접관 회신 요청 초대
- `reminder_l1`: 24h 정중 리마인더
- `reminder_l2`: 48h 마감 강조
- `reminder_l3`: 68h 최종 알림 + 상급자 CC
- `applicant_invite`: 지원자 면접 안내
- `applicant_change`: 지원자 일정 변경 안내
- `applicant_defer`: 지원자 다음 회차 이월 안내
- `interviewer_confirm`: 면접관 스케줄 확정 안내
- `hr_alert_integrity`: HR 무결성 위반 경고
- `hr_alert_repair`: HR 재편성 필요 경고

---

## A — API

### 단건 발송
```
POST /api/v1/notify/send
body:
{
  "template_id": "reminder_l1",
  "channel": "email",
  "recipient": "iv1@lge.com",
  "cc": ["backup@lge.com"],
  "context": {
    "name": "이지훈",
    "deadline": "2026-07-31 18:00",
    "form_link": "https://hr.lge.com/form/abc123"
  },
  "correlation_id": "R2026-Q3-01/invitee-abc"
}

response 202:
{"data": {"notification_id":"...", "status":"queued"}}
```

### 다중 발송
```
POST /api/v1/notify/broadcast
body:
{
  "template_id": "applicant_invite",
  "channel": "email",
  "recipients": [
    {"email":"a1@x.com", "context":{"name":"새한별", ...}},
    ...
  ],
  "correlation_id": "R2026-Q3-01"
}
```

### 발송 이력
```
GET /api/v1/notify/history?correlation_id=R2026-Q3-01
GET /api/v1/notify/history/{recipient}
```

### 열람 추적 (Pixel)
```
GET /api/v1/notify/track/open/{notification_id}.png
→ 1x1 투명 PNG, 열람 기록
```

### 템플릿 관리
```
GET  /api/v1/notify/templates
GET  /api/v1/notify/templates/{template_id}
PUT  /api/v1/notify/templates/{template_id}
     body: {"channel":"email", "subject":"...", "body":"Jinja2..."}
```

### 채널 관리
```
GET  /api/v1/notify/channels
POST /api/v1/notify/channels
PUT  /api/v1/notify/channels/{channel_id}/toggle    # enabled 스위치
```

---

## D — Design

### 이벤트 발행
- `NOTIFICATION_SENT`
- `NOTIFICATION_FAILED`
- `NOTIFICATION_OPENED` (Pixel 열람 시)

### 구독 이벤트 (자동 발송 트리거)
- `DISTRIBUTION_APPROVED` → `applicant_invite` 대량 발송
- `REQUEST_SENT` → `invite` 발송 (Service 03이 위임)
- `REMINDER_SENT` → 실제 발송 처리 (Service 03이 로직 결정, 발송은 여기서)
- `SCHEDULE_LOCKED` → `interviewer_confirm` + `applicant_invite` 발송
- `REPAIR_EXECUTED` → `applicant_change` 또는 `applicant_defer` 발송
- `INTEGRITY_VIOLATED` → `hr_alert_integrity` HR팀 발송

### 발송 파이프라인
1. 요청 수신 → `notifications` 테이블에 `queued` 상태 저장
2. 백그라운드 워커가 큐에서 pull
3. 채널별 어댑터로 발송 (SMTP · Slack Webhook · SMS Gateway)
4. 성공 → `sent`, 실패 → 재시도(최대 3회) → `failed`
5. 이벤트 발행

### 재시도 정책
- 1회차: 즉시
- 2회차: 30초 후
- 3회차: 5분 후
- 그 이후: dead letter 큐

### 프로젝트 구조
```
notification-hub/
├── app/
│   ├── main.py
│   ├── api/{notify.py, templates.py, channels.py, track.py}
│   ├── domain/{notification.py, template.py}
│   ├── services/
│   │   ├── dispatcher.py         # 채널 라우팅
│   │   ├── retry_worker.py       # 재시도 백그라운드
│   │   ├── template_renderer.py  # Jinja2
│   │   └── channels/
│   │       ├── email_smtp.py
│   │       ├── email_sendgrid.py
│   │       ├── slack_webhook.py
│   │       └── sms_stub.py
│   ├── infrastructure/{db.py, event_bus.py, queue.py}
│   └── events.py
```

### 테스트 요구사항
- `test_template_render`: Jinja2 변수 정상 치환
- `test_email_send`: 로컬 SMTP mock으로 발송 성공
- `test_retry`: 실패 시 3회 재시도 후 dead letter
- `test_broadcast`: 100명 발송 시 개별 알림 100건 생성
- `test_open_tracking`: Pixel 접근 시 opened_at 기록

### 완료 판정 체크리스트
- [ ] SMTP · Slack · SMS 3개 채널 어댑터 구현 (SMS는 stub 허용)
- [ ] 재시도 3회 후 dead letter
- [ ] `NOTIFICATION_SENT` 이벤트 발행 확인
- [ ] Pixel 열람 추적 동작
- [ ] 10개 기본 템플릿 seed 데이터 삽입
