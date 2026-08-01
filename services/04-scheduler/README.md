# Service 04 — Scheduler

면접 일정 배치 엔진. PPT 4대 규칙을 지키면서 지원자를 날(1일차…) × 시간대 × 면접관 슬롯에 배치한다.

- 포트: **8004** · DB 스키마: **sched_db**
- BMAD 명세: `../../bmad/04_scheduler.md` · 공통 계약: `../../bmad/00_SHARED_CONTRACT.md`

## 실행

```bash
pip install -r requirements.txt

# 로컬 실행 (Docker 불필요 — SQLite + fakeredis PoC 모드)
uvicorn app.main:app --port 8004 --reload

# 테스트 (커버리지 70% 미만이면 실패)
pytest
```

`.env`의 PoC 설정: `DATABASE_URL=sqlite:///./sched_db.sqlite`, `REDIS_URL=fakeredis://`,
`USE_MOCK=true`, `STORAGE_DIR=./storage`. 기동 시 테이블이 자동 생성되고,
면접관 테이블이 비어 있으면 목 데이터(5팀 × 4명 = 20명)로 시드된다.

## 알고리즘 3종

| | 전략 | 커버리지 | 규칙 준수(overall) |
|---|---|---|---|
| **v1** | 면접관 우선 · 최단 적합 그리디 | 100% | 83.3 |
| **v4** | 2단계 계층적 (팀당 면접일 2일) | 68.2% | 100 |
| **v5** | v4 완화(면접일 3일) + Stage 3 Fallback | 100% | 100 |

*지원자 88명 / 면접관 20명 기준 실측값. 세 알고리즘 모두 하드 위반 0건, 실행 1ms 미만.*

- **v1** — 우선순위 상위(대학원·타겟랩) 지원자가 1일차에 몰려 규칙1이 무너진다. 비교 기준선.
- **v4** — Stage 1(팀×날) → Stage 1b(날별 학사/대학원 쿼터) → Stage 2(연속 시간대 배치).
  규칙은 완벽하지만 팀당 수용량이 이틀 × 8타임 = 16슬롯으로 제한돼 커버리지가 떨어진다.
- **v5** — Stage 1을 사흘로 완화(팀당 18슬롯, 5팀 = 90 ≥ 88명)하고, 남는 인원은
  Stage 3 Fallback이 세로 연속을 깨지 않고 날별 대학원 비율을 목표에 가깝게 유지하는 슬롯에 흡수한다.

## 4대 규칙 점수 정의

`overall`은 네 점수의 산술 평균 (명세 예시 60/100/100/100 → 90.0과 일치).

| 규칙 | 종류 | 점수 산식 |
|---|---|---|
| `rule1_grad_balance` | SOFT | **(팀, 면접일)별** 대학원 비율이 `target ± tolerance`(기본 ±20%p) 안에 드는 칸의 비율. `target`을 안 주면 **그 팀 명단의 실제 비율**이 목표다 — 재는 것은 "요일 분산"이지 3할이 아니라서, 대학원생이 1할인 팀에 3할을 들이대면 고르게 나눠도 0점이 나온다. 편중은 팀 안에서 생기므로 회차 전체의 날별 비율로는 재지 않는다(팀마다 면접일이 다르면 튄다 — 참고용으로 `day_ratios`에 함께 담는다). 면접일이 하루뿐인 팀은 나눌 날이 없어 평가에서 뺀다. 인사가 숫자를 못 박으면 그 값을 저장해 두고 검증할 때도 같은 값으로 잰다 |
| `rule2_team_conflict` | **HARD** | `100 × (1 − 같은 팀 동시간 중복 건수 / 전체 배정 수)` |
| `rule3_vertical_group` | SOFT | (팀, 날)별 사용 시간대가 연속인 그룹의 비율 — Webex 재입장 최소화 |
| `rule4_first_slot` | SOFT | 오전 · 오후 첫 타임을 두 가지로 본 통과 비율 — ① 첫 칸의 동시 진행 건수가 그 덩어리의 다른 칸 이하 ② 첫 칸에 앉은 조가 그 덩어리의 조들보다 작음(조 크기 = 그날 그 조가 보는 인원). 어느 칸이 첫 칸인지는 면접 진행 조건이 정하고, 점유 시간대가 2개 미만인 덩어리는 평가 제외 |

## 하드 제약

`Board.place()`를 통과해야만 배치되므로 구조적으로 위반이 발생하지 않는다.
외부에서 들어온 시간표는 `POST /validate`로 검사한다.

`TEAM_CONFLICT` · `INTERVIEWER_CONFLICT` · `DAILY_LIMIT_EXCEEDED`(max_daily / 8타임 상한) ·
`AVAILABILITY_VIOLATION` · `UNKNOWN_SLOT`

## 락 시스템

`DRAFT → CONFIRMED → LOCKED` 단방향, **한 단계씩만** 상승.
강등·단계 건너뛰기·동일 단계 재적용은 모두 `400 VALIDATION_FAILED`.
`applicant_ids`를 주면 부분 락이 가능하며, 대상 중 하나라도 전이 불가면 전부 롤백된다(원자적).
스케줄 전체 `status`는 가장 낮은 배정 락 레벨을 따른다.

## API

```
POST /api/v1/schedules/generate            # algorithm: v1 | v4_hierarchical | v5
GET  /api/v1/schedules/{id}
GET  /api/v1/schedules/{id}/heatmap        # 날 × 시간 히트맵
GET  /api/v1/schedules/{id}/by-team        # 팀별 그룹핑
GET  /api/v1/schedules/{id}/rules          # ?recompute=true 로 재계산
POST /api/v1/schedules/{id}/validate       # 하드 위반 + 소프트 페널티
POST /api/v1/schedules/{id}/lock

GET  /api/v1/interviewers?team=AI솔루션팀
POST /api/v1/interviewers
PUT  /api/v1/interviewers/{id}             # 가용성 업데이트
GET  /api/v1/rounds/{round_id}/readiness   # 구독 이벤트로 파악한 회차 준비 상태

GET  /healthz · GET /metrics (Prometheus)
```

응답 규약: `{"data": ..., "error": null}` / `{"data": null, "error": {"code","message"}}`

## 이벤트

`shared/contracts/events.py`의 `EventEnvelope`를 그대로 사용하며 공용 채널 `hr.events`로 발행한다.

- **발행** — `SCHEDULE_GENERATED`(생성 완료) · `SCHEDULE_LOCKED`(락 상승) · `RULE_VIOLATED`(하드 위반)
- **구독** — `RESPONSE_RECEIVED`(03, 가용성 확정) · `DISTRIBUTION_APPROVED`(02, 명단 확정)

버스는 인프로세스 디스패치 + Redis 발행을 함께 하고, 리스너 스레드는 `event_id`로 중복을 걸러
자기 발행분을 두 번 처리하지 않는다.

## 구조

```
app/
├── main.py                     FastAPI 앱 · lifespan · 예외 핸들러 · /metrics
├── config.py                   .env 로드
├── errors.py                   공통 에러 규약
├── events.py                   이벤트 발행/구독
├── api/                        schedules · rules · interviewers
├── domain/                     schedule · assignment · interviewer(ORM) · schemas(DTO)
├── infrastructure/             db · event_bus · response_client · contracts(공통 계약 브리지)
└── services/
    ├── algorithm_v1/v4/v5.py   배치 알고리즘
    ├── hierarchical.py         v4·v5 공유 스테이지
    ├── board.py                하드 제약을 강제하는 배치 엔진
    ├── rule_evaluator.py       4대 규칙 스코어
    ├── constraint_checker.py   하드/소프트 검증
    ├── lock_manager.py         3단계 락
    ├── schedule_service.py     오케스트레이션
    └── mock_data.py            PoC 목 데이터
```

## PostgreSQL 전용 타입 매핑 (SQLite PoC)

`UUID → String(36)`, `JSONB → JSON`, `TEXT[] → JSON 배열`.
지원자 원본은 Service 02 소유이므로 규칙 평가에 필요한 `team`·`degree`만
`assignments`에 비정규화해 저장한다.

## 완료 판정 체크리스트

- [x] `POST /schedules/generate` 3초 이내 응답 — 실측 1ms 미만 (`test_generate_responds_within_3_seconds`)
- [x] `algorithm=v4` 실행 시 `rule_compliance.overall ≥ 85` — 실측 100 (`test_v4_overall_at_least_85`)
- [x] `algorithm=v5` 실행 시 `coverage_pct ≥ 90` AND `overall ≥ 85` — 실측 100 / 100 (`test_v5_coverage_90_and_overall_85`)
- [x] 락 레벨 강등 시도는 400 에러 (`test_lock_downgrade_returns_400`)
- [x] `SCHEDULE_LOCKED` 이벤트 발행 — 공용 채널 수신 확인 (`test_schedule_locked_reaches_downstream_subscribers`).
      Service 05·06의 실제 소비는 해당 서비스 소관.

테스트 112개 통과 · 커버리지 95.7%.
