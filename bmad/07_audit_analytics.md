# 📦 Service 07 — Audit & Analytics

> **필수 참조**: `00_SHARED_CONTRACT.md`
> **담당 단계**: 공통 인프라 (전 단계)
> **포트**: 8007 · **DB 스키마**: `audit_db`

---

## B — Business Context

### 해결하는 문제
- 이벤트가 여러 서비스에 흩어져 있어 통합 감사 불가
- HR 담당자에게 “회차 종합 리포트”가 없음
- Before/After 지표 비교가 수작업
- v2 대시보드의 협업 온도계·위험 신호 감지가 실시간 데이터 없이 렌더링됨

### 비즈니스 가치
- 모든 이벤트를 append-only 로그로 수집 → 완전한 감사 가능
- 실시간 KPI 대시보드 (v2 대시보드 백엔드)
- 회차별 종합 리포트 자동 생성
- Before/After 개선 지표 자동 계산

### 성공 기준
- 이벤트 수신 지연 1초 이내
- 대시보드 API 응답 500ms 이내
- 회차 리포트 생성 10초 이내

---

## M — Model

### 테이블 스키마

```sql
-- 모든 서비스의 이벤트를 append-only로 저장
CREATE TABLE event_log (
    log_id          BIGSERIAL PRIMARY KEY,
    event_id        UUID NOT NULL,
    event_type      VARCHAR(64) NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    round_id        VARCHAR(32),
    producer        VARCHAR(64),
    correlation_id  VARCHAR(64),
    payload         JSONB NOT NULL,
    received_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_event_type ON event_log(event_type, timestamp);
CREATE INDEX idx_event_round ON event_log(round_id, timestamp);
CREATE INDEX idx_event_correlation ON event_log(correlation_id);

-- 시점별 KPI 스냅샷
CREATE TABLE kpi_snapshots (
    snapshot_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id        VARCHAR(32),
    metric_name     VARCHAR(64),
    value           FLOAT,
    labels          JSONB,
    captured_at     TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_kpi_round_metric ON kpi_snapshots(round_id, metric_name, captured_at);

-- 조직별 응답 패턴 집계 (Service 03 데이터 캐시)
CREATE TABLE org_response_stats (
    round_id        VARCHAR(32),
    org             VARCHAR(64),
    mean_hours      FLOAT,
    completion_rate FLOAT,
    predicted_slow  BOOLEAN,
    updated_at      TIMESTAMPTZ,
    PRIMARY KEY (round_id, org)
);

-- 회차 리포트 캐시
CREATE TABLE reports (
    report_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id        VARCHAR(32),
    report_type     VARCHAR(32),         -- 'round_summary'|'before_after'|'audit'
    content         JSONB,
    generated_at    TIMESTAMPTZ DEFAULT NOW()
);
```

### 수집 대상 이벤트 (전체)
`00_SHARED_CONTRACT.md`의 카탈로그 전 18종 이벤트 모두 구독 · 저장.

---

## A — API

### 실시간 KPI (v2 대시보드 백엔드)
```
GET /api/v1/dashboard/kpi?round_id=R2026-Q3-01

response 200:
{
  "data": {
    "총_대상자": 88,
    "회신_완료": 42,
    "회신_대기": 6,
    "배치_완료율": 89,
    "규칙_준수율": 90.5,
    "위험도": "Medium",
    "실행_시간_초": 3.2
  }
}
```

### 조직별 협업 온도
```
GET /api/v1/dashboard/organizations?round_id=R2026-Q3-01

response 200:
{
  "data": [
    {"org":"제1기술원","avg_response_h":6,"completion":95,"temperature":"cool"},
    {"org":"제3기술원","avg_response_h":52,"completion":62,"temperature":"hot"}
  ]
}
```

### 위험 신호 감지
```
GET /api/v1/dashboard/risks?round_id=R2026-Q3-01

response 200:
{
  "data": [
    {"type":"담당자_변경","team":"3기술원","severity":"medium"},
    {"type":"노쇼_예측","count":3,"severity":"low"},
    {"type":"면접관_피로도","interviewer":"이OO","severity":"high"}
  ]
}
```

### 이벤트 타임라인
```
GET /api/v1/audit/timeline?round_id=R2026-Q3-01&event_type=SCHEDULE_LOCKED

response 200:
{
  "data": [
    {"event_id":"...","timestamp":"...","producer":"scheduler","payload":{...}}
  ]
}
```

### 회차 종합 리포트
```
GET /api/v1/reports/rounds/{round_id}

response 200:
{
  "data": {
    "round_id": "R2026-Q3-01",
    "duration_hours": 3.2,
    "phases": [
      {"phase":"자료취합","start":"...","duration_h":0.1},
      {"phase":"배포","duration_h":0.3},
      {"phase":"회신수집","duration_h":1.8},
      {"phase":"배치","duration_h":0.05},
      {"phase":"안내","duration_h":0.1}
    ],
    "rule_compliance": {...},
    "noshow_count": 3,
    "repair_events": 1
  }
}
```

### Before/After 비교
```
GET /api/v1/reports/before-after?rounds=R2025-Q4-04,R2026-Q3-01

response 200:
{
  "data": {
    "회신_소요시간_h": {"before":29.9,"after":11.8,"delta_pct":-60.5},
    "회신_완료율": {"before":86,"after":92,"delta_pp":6.0},
    "배치_소요_h": {"before":32,"after":0.05,"delta_pct":-99.8},
    "규칙_준수율": {"before":65,"after":90,"delta_pp":25},
    "노쇼_대응_시간_h": {"before":8,"after":0.1,"delta_pct":-98.8}
  }
}
```

### 감사 감정 (Audit Query)
```
POST /api/v1/audit/query
body:
{
  "round_id": "...",
  "actor": "HR김민지",
  "event_types": ["DISTRIBUTION_APPROVED","SCHEDULE_LOCKED"],
  "from": "2026-07-29T00:00:00Z",
  "to": "2026-07-31T23:59:59Z"
}
```

---

## D — Design

### 이벤트 수집 방식
- Redis Pub/Sub의 wildcard 채널 `hr.*` 구독
- 수신 즉시 `event_log`에 저장
- 이벤트 타입별 프로젝션(projection) 생성 → `kpi_snapshots`

### 프로젝션 규칙 (예시)
| 이벤트 | 업데이트 대상 KPI |
|---|---|
| `RESPONSE_RECEIVED` | `회신_완료`, 조직별 평균 응답시간 |
| `SCHEDULE_LOCKED` | `배치_완료율`, `규칙_준수율`, `실행_시간_초` |
| `REPAIR_EXECUTED` | 노쇼 대응 이력, `위험도` |
| `INTEGRITY_VIOLATED` | 위험 신호 알림 |

### 리포트 생성 파이프라인
1. `GET /reports/rounds/{round_id}` 호출
2. `event_log`에서 해당 회차 이벤트 전체 로드
3. 단계별 지속 시간 계산 (`MASTER_REGISTERED` → `DISTRIBUTION_APPROVED` 등)
4. Service 04의 규칙 준수율 조회 (API 호출)
5. JSON 리포트 생성 → `reports` 캐시 저장
6. 재요청 시 캐시에서 즉시 반환 (invalidate 조건: 동일 회차 새 이벤트 도착)

### 프로젝트 구조
```
audit-analytics/
├── app/
│   ├── main.py
│   ├── api/{dashboard.py, reports.py, audit.py}
│   ├── domain/{event.py, kpi.py, report.py}
│   ├── services/
│   │   ├── event_collector.py    # Redis 구독
│   │   ├── projector.py          # 이벤트 → KPI
│   │   ├── report_generator.py
│   │   ├── risk_detector.py      # 위험 신호 룰 엔진
│   │   └── clients/              # 다른 서비스 API 호출
│   │       ├── scheduler_client.py
│   │       └── response_client.py
│   ├── infrastructure/{db.py, event_bus.py}
│   └── events.py
```

### 위험 신호 룰 엔진 (예시)
```python
RISK_RULES = [
    ("담당자_변경",     lambda e: is_new_responder(e)),
    ("노쇼_예측",       lambda a: predict_noshow_score(a) > 0.7),
    ("면접관_피로도",   lambda i: consecutive_high_load(i, weeks=3)),
    ("조직_회신_지연",  lambda o: o.mean_hours > 40),
]
```

### 테스트 요구사항
- `test_event_ingest`: 이벤트 수신 → event_log 저장
- `test_projection`: RESPONSE_RECEIVED 이벤트 → 회신완료 카운터 증가
- `test_report_generation`: 회차 리포트 10초 이내 생성
- `test_before_after`: 두 회차 비교 리포트 정확도
- `test_cache_invalidation`: 새 이벤트 도착 시 캐시 무효화

### 완료 판정 체크리스트
- [ ] 18종 이벤트 모두 수신·저장 확인
- [ ] `/dashboard/kpi` 응답 500ms 이내
- [ ] `/reports/rounds/{id}` 10초 이내
- [ ] Before/After 리포트에서 지금까지 검증된 수치 재현 (회신 -60%, 규칙 준수 +25pp)
- [ ] 위험 신호 4종 감지 로직 동작
