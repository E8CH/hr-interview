# Service 05 — Repair Engine

노쇼·취소 발생 시 **하드 제약 위반 0건**을 보장하며 시간표를 재편성하고,
HR 담당자에게 Plan A/B/C 3가지 대안을 자동 제시한다.

- 포트: **8005** · DB 스키마: **repair_db**
- 명세: `../../bmad/05_repair_engine.md` · 공통 계약: `../../bmad/00_SHARED_CONTRACT.md`

---

## 실행 (Docker 불필요 · PoC 모드)

```bash
pip install -r requirements.txt

# .env 기본값: SQLite 파일 + fakeredis 인메모리 + 다른 서비스 mock
uvicorn app.main:app --port 8005 --reload
```

앱 기동 시 `init_db()` 가 스키마를 자동 생성하므로 PoC 는 위 두 줄로 끝난다.
스키마를 명시적으로 관리하려면 [마이그레이션](#마이그레이션-alembic) 참고.

| 항목 | PoC 값 | 프로덕션 |
|---|---|---|
| DB | `sqlite:///./repair_db.sqlite` | PostgreSQL `repair_db` |
| 이벤트 버스 | `fakeredis://` (인메모리) | Redis Pub/Sub |
| 파일 저장 | `./storage/` | MinIO |
| Service 04 | `USE_MOCK=true` → 합성 시간표 | `SCHEDULER_BASE_URL` REST 호출 |

Swagger UI: <http://localhost:8005/docs>

### 테스트 · 완료 판정

```bash
pytest tests/ --cov=app --cov-report=term-missing   # 82 tests · 97% coverage
python verify_checklist.py                          # 명세의 완료 판정 5항목 자동 검증
```

---

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/v1/repair/noshow` | 노쇼 통보 → 202 + Plan A/B/C 자동 생성 |
| POST | `/api/v1/repair/cancel` | 지원자·면접위원 취소 통보 |
| GET | `/api/v1/repair/plans/{event_id}` | 대안 3가지 조회 |
| POST | `/api/v1/repair/plans/{event_id}/select` | Plan 선택·적용 + 이벤트 발행 |
| GET | `/api/v1/repair/audit/{round_id}` | 재편성 감사 로그 |
| GET | `/api/v1/repair/locks/{schedule_id}` | 락 상태 조회 |
| POST | `/api/v1/repair/locks/upgrade` | 락 승격 (강등 불가) |
| GET | `/healthz` · `/metrics` | 헬스체크 · Prometheus 메트릭 |

응답 봉투는 공통 규약을 따른다: `{"data": ..., "error": null}`

```bash
# 1) 노쇼 통보
curl -X POST http://localhost:8005/api/v1/repair/noshow \
  -H "Content-Type: application/json" \
  -d '{"round_id":"R2026-Q3-01","schedule_id":"SCH-DEMO",
       "noshow_applicant_ids":["3300002","3300007","3300012"],
       "reported_by":"HR김민지"}'
# -> 202 {"data":{"event_id":"...","status":"pending","plan_count":3}}

# 2) 대안 조회
curl http://localhost:8005/api/v1/repair/plans/{event_id}

# 3) Plan 선택·적용
curl -X POST http://localhost:8005/api/v1/repair/plans/{event_id}/select \
  -H "Content-Type: application/json" \
  -d '{"plan_id":"...","selected_by":"HR김민지"}'
# -> 200 {"data":{"applied":true,"affected_assignments":13,"hard_violations":0}}
```

---

## 재편성 알고리즘 (v3.1)

`app/services/safe_repair.py`

1. **LOCKED** 상태 대상자는 즉시 다음 회차 이월 — 절대 이동하지 않는다
2. 나머지 대상자에게 예비 슬롯 재예약 시도
3. **각 후보 슬롯마다 하드 제약 재검증** ← v3 대비 핵심 수정점
4. 위반이 생기는 슬롯은 스킵
5. 팀 일치 우선, 실패 시 이월

재검증(`app/services/constraint_recheck.py`)은 2중이다.
슬롯 선택 시 증분 인덱스로 O(1) 판정하고, 완성된 Plan 은 전수 검사로 다시 확인한다.
증분 판정이 잘못되더라도 전수 검사가 재예약을 되돌려 **위반 0건을 강제**한다
(`tests/test_safety_net.py::test_rollback_when_incremental_check_is_broken`).

### 검증하는 제약

| 코드 | 종류 | 내용 |
|---|---|---|
| `RULE2_TEAM_CONFLICT` | HARD | 같은 팀 동시간 중복 금지 |
| `H2_INTERVIEWER_DOUBLE_BOOK` | HARD | 면접위원 동시간 이중 예약 |
| `H3_APPLICANT_DOUBLE_BOOK` | HARD | 지원자 동시간 이중 예약 |
| `H4_MAX_DAILY_EXCEEDED` | HARD | 면접위원 일일 최대 초과 |
| `RULE1_GRAD_BALANCE` | SOFT | (팀, 날)별 대학원 비율이 **그 팀**의 비율 ±20%p — 3할 같은 고정값도, 회차 전체의 날별 비율도 아니다 (04 규칙1과 같은 잣대) |
| `RULE3_VERTICAL_GROUP` | SOFT | 동일 팀 세로 연속 배치 |
| `RULE4_FIRST_SLOT` | SOFT | 오전 · 오후 첫 타임은 그날 적게 보는 조 우선 (어느 칸이 첫 칸인지는 면접 진행 조건이 정한다) |

## Plan A/B/C

| Plan | 전략 | 특징 |
|---|---|---|
| `A_safe` | 예비 슬롯 · **팀 일치만** | cross-team 0건 보장 |
| `B_defer` | 전원 다음 회차 이월 | 이번 회차 시간표 무변경 |
| `C_cross_team` | 팀 일치 우선 후 cross-team 허용 | 재예약률 최대, `warning` 으로 건수 고지 |

세 Plan 모두 하드 위반 0 을 만족해야 제시된다. 위반이 남는 Plan 은 폐기된다.

## 3단계 락 시스템

```
DRAFT(0)      자유롭게 이동
CONFIRMED(1)  이동 허용 + 소프트 페널티
LOCKED(2)     재편성 불가 — 이미 안내된 지원자는 절대 흔들지 않는다
```

- 락은 **승격만** 가능하다. 강등 요청은 `skipped` 로 반환된다.
- 유효 락 레벨 = `lock_map` 테이블 > 시간표 스냅샷 값 (승격이 항상 우선)

## 이벤트

**발행**

| 이벤트 | 시점 |
|---|---|
| `NOSHOW_REPORTED` | 노쇼 API 접수 시 |
| `REPAIR_EXECUTED` | Plan 선택·적용 완료 |
| `PARTICIPANT_DEFERRED` | 이월 대상 발생 |
| `SLOT_REOPENED` | 노쇼로 슬롯 반환 |

**구독**

| 이벤트 | 처리 |
|---|---|
| `SCHEDULE_LOCKED` (Service 04) | 재편성 대상 시간표 스냅샷 선적재 |

봉투는 `shared/contracts/events.py` 의 `EventEnvelope` 를 그대로 사용하며,
한 재편성 체인의 모든 이벤트는 동일한 `correlation_id` 를 공유한다.

> `SLOT_REOPENED` 는 05 명세에는 있으나 `00_SHARED_CONTRACT.md` 의 이벤트 카탈로그에는
> 없다. 공통 계약(읽기 전용)을 수정하지 않기 위해 `app/events.py` 의 로컬 상수로 정의하고
> 봉투 규격만 준수한다.

---

## 구조

```
app/
├── main.py                      FastAPI 앱 · 공통 에러 핸들러 · /metrics
├── config.py                    .env 로드
├── events.py                    이벤트 발행/구독
├── api/       repair · plans · locks · audit · schemas
├── domain/    repair_event · repair_plan · schedule
├── services/  safe_repair · plan_generator · constraint_recheck
│              scheduler_client · lock_service · repair_service
└── infrastructure/  db (SQLAlchemy 2.x) · event_bus (fakeredis/Redis)

migrations/                      Alembic (alembic.ini · env.py · versions/)
tests/                           82 tests
verify_checklist.py              완료 판정 자동 검증
```

### 테이블

`repair_events` · `repair_plans` · `selected_plans` · `lock_map`
\+ `schedule_snapshots` (Service 04 시간표의 로컬 사본 — 다른 서비스 DB 직접 접근 금지 규약 준수)

PostgreSQL DDL 의 `UUID` → `String(36)`, `JSONB` → `JSON` 으로 SQLite 대응.

조회 패턴에 맞춰 인덱스를 둔다:
`repair_events.round_id` (감사 로그) · `repair_plans.event_id` · `selected_plans.event_id`

### 마이그레이션 (Alembic)

접속 URL 은 `.env` 의 `DATABASE_URL` 을 따른다 — `migrations/env.py` 가 `app.config` 에서
읽으므로 SQLite(PoC) 와 PostgreSQL(프로덕션)이 **동일한 명령**을 쓴다.

```bash
alembic upgrade head                        # 스키마 생성 · 최신화
alembic current                             # 현재 리비전
alembic check                               # 모델 ↔ 마이그레이션 드리프트 검사
alembic downgrade base                      # 전체 롤백
alembic revision --autogenerate -m "설명"    # 모델 변경 후 새 리비전
```

| 리비전 | 내용 |
|---|---|
| `0001_initial_repair_schema` | 5개 테이블 + 인덱스 3개 (`upgrade`/`downgrade` 왕복 검증 완료) |

SQLite 는 `ALTER` 지원이 제한적이라 `env.py` 에서 `render_as_batch` 를 켠다.

> `alembic.ini` 는 **ASCII 전용**으로 유지할 것. Alembic 이 configparser 로 시스템 로케일
> 인코딩(한글 Windows 는 cp949)을 써서 읽기 때문에 한글 주석이 들어가면 깨진다.
