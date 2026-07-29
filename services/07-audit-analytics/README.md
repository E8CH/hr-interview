# Service 07 — Audit & Analytics

18종 이벤트를 **append-only**로 수집하고, 이를 KPI·조직 온도계·위험 신호·회차
리포트로 투영하는 서비스.

- 포트: **8007**
- BMAD 명세: `../../bmad/07_audit_analytics.md`
- 공통 계약: `../../bmad/00_SHARED_CONTRACT.md`
- **이벤트 발행: 없음 (수집 전용)** / 구독: `*` wildcard (전 서비스)

## 빠른 시작 (Docker 불필요)

```bash
pip install -r ../../requirements.txt

# 1) 데모 데이터 주입 (18종 이벤트 + Before 회차 baseline)
python scripts/seed_poc.py

# 2) 실행
uvicorn app.main:app --port 8007 --reload

# 3) 확인
curl "http://127.0.0.1:8007/api/v1/dashboard/kpi?round_id=R2026-Q3-01"
```

`.env`(이미 존재)로 동작 모드를 바꾼다.

| 변수 | PoC 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./audit_db.sqlite` | 파일 기반 SQLite |
| `REDIS_URL` | `fakeredis://` | 인메모리 이벤트 버스 |
| `USE_MOCK` | `true` | 다른 서비스 호출을 mock으로 대체 |
| `ENABLE_COLLECTOR` | `true` | Redis 구독 루프 기동 여부 |
| `EVENT_CHANNEL_PATTERN` | `*` | psubscribe 패턴 (좁히려면 `hr.*` 등으로 지정) |
| `EVENT_CHANNEL_PREFIX` | `hr` | 07이 채널명을 만들 때 쓰는 프리픽스 |
| `STORAGE_DIR` | `./storage` | 로컬 파일 저장 |

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/dashboard/kpi?round_id=` | 실시간 KPI 7종 |
| GET | `/api/v1/dashboard/organizations?round_id=` | 조직별 협업 온도계 |
| GET | `/api/v1/dashboard/risks?round_id=` | 위험 신호 4종 |
| GET | `/api/v1/audit/timeline?round_id=&event_type=` | 이벤트 타임라인 (시간 오름차순) |
| POST | `/api/v1/audit/query` | 회차·행위자·타입·기간 복합 감사 질의 |
| POST | `/api/v1/audit/events` | **PoC 전용** — 타 서비스 이벤트 mock 주입 |
| GET | `/api/v1/audit/events/stats` | 18종 수집 커버리지 |
| GET | `/api/v1/reports/rounds/{round_id}` | 회차 종합 리포트 (`X-Cache: HIT\|MISS`) |
| GET | `/api/v1/reports/before-after?rounds=b,a` | Before/After 비교 |
| GET | `/healthz`, `/metrics` | 헬스체크 · Prometheus 텍스트 |

응답 봉투는 공통 계약을 따른다 — `{"data": ..., "error": null}` /
`{"data": null, "error": {"code","message"}}`.

## 다른 서비스 이벤트를 mock으로 주입하기

두 경로 모두 **같은 수집 함수**(`ingest_event`)를 통과하므로 동작이 동일하다.

```bash
# (a) HTTP 주입 — 단건 또는 배열
curl -X POST http://127.0.0.1:8007/api/v1/audit/events \
  -H "Content-Type: application/json" \
  -d '{"event_type":"RESPONSE_RECEIVED","round_id":"R2026-Q3-01",
       "producer":"response-collector",
       "payload":{"org":"제1기술원","invitee_id":"IV101","response_hours":6.0}}'
```

```python
# (b) 이벤트 버스 발행 — 실제 구독 경로
from app.infrastructure.event_bus import get_event_bus
await get_event_bus().publish("hr.RESPONSE_RECEIVED", json.dumps(envelope))
```

라우팅은 채널 이름이 아니라 **봉투의 `event_type`** 으로만 한다. 공통 계약이
채널 명명 규칙을 정하지 않아 발행 측 스킴이 3종으로 갈려 있어서
(`hr-events` 고정 / `event_type` 그대로 / 설정값), 07은 `*`로 넓게 받고
봉투로 거른다. `event_type`이 없는 메시지는 `invalid`로 버려진다.

> ⚠️ **PoC 한계** — `REDIS_URL=fakeredis://`는 **프로세스 메모리 안에서만** 동작한다.
> 서비스를 각각 다른 창에서 띄우면 FakeRedis 인스턴스도 따로 생기므로,
> 01/02/04가 발행한 이벤트는 07에 **원천적으로 도달하지 못한다**
> (`tools/mismatch_report.md` M-8). 실제로 흐르게 하려면 둘 중 하나가 필요하다.
>
> 1. 7개 서비스 전부 `REDIS_URL=redis://localhost:6379`로 통일 — 07은 코드 변경 없이 동작
> 2. 발행 측이 `POST /api/v1/audit/events`로 봉투를 직접 POST (위 (a) 경로)

## 테스트

```bash
pytest tests/ --cov=app --cov-report=term-missing
```

현재 **119 passed · 커버리지 96%** (요구치 70%).

명세가 요구한 테스트: `test_event_ingest` · `test_projection` ·
`test_report_generation` · `test_before_after` · `test_cache_invalidation`.
완료 판정 체크리스트 5개 항목은 `tests/test_checklist.py`가 그대로 검증한다.

## 설계 메모

- **append-only** — `EventRepository`에는 UPDATE/DELETE 메서드가 아예 없다.
  KPI·조직 통계·리포트는 전부 파생 데이터이며 이벤트에서 언제든 재계산된다.
- **멱등 수집** — `event_log.event_id` UNIQUE. at-least-once 재전송은 `duplicate`로 흘린다.
- **방어적 payload 읽기** — 봉투만 계약으로 신뢰하고, payload는 `pick_*` 헬퍼로
  다중 키 폴백(`org`/`team`/`team_name` …)을 거쳐 읽는다.
- **리포트 캐시** — `reports.generated_at < MAX(event_log.received_at)`이면 무효.
- **타 서비스 장애 격리** — Service 03/04 클라이언트는 실패 시 mock 폴백하므로
  상대가 죽어 있어도 리포트가 생성된다.
- **PostgreSQL→SQLite 매핑** — `BIGSERIAL`→Integer, `UUID`→String(36),
  `JSONB`→JSON, `TIMESTAMPTZ`→naive UTC DateTime.

## 명세 예시 수치와의 차이 (의도된 선택)

명세의 예시 표들끼리 서로 어긋나는 지점이 있어, **완료 판정 체크리스트를
우선**하고 아래처럼 정했다.

| 항목 | 채택값 | 근거 |
|---|---|---|
| 규칙_준수율 | 90.5 | KPI 예시(90.5) 채택. Before/After 예시의 90은 반올림 표기로 봄 → `delta_pp` 25.5 (체크리스트 "+25pp") |
| 회신_완료 / 대기 | 46 / 4 | 예시의 42/6은 42÷48=87.5%로 요구치 92%와 불일치. 50 초대·46 회신이 92%를 정확히 만족 |
| 배치_완료율 | 88.6 | 78/88. 정수 표기 시 89 — 예시와 일치 |
| 위험도 | High | 최대 severity 규칙. 명세의 risks 예시에 `high` 신호(면접관 피로도)가 있으므로 Medium과 모순 |
| 실행_시간_초 3.2 vs 회신 11.8h | 둘 다 재현 | 전자는 회차 처리 벽시계, 후자는 면접위원 관점 요청→회신 경과. 측정 기준이 다름 |
