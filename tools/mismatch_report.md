# 계약 대조 리포트 (Contract Mismatch Report)

- 대상: `bmad/00_SHARED_CONTRACT.md` ↔ `tools/api_inventory.json` (실제 `/openapi.json` 스캔 결과)
- 스캔 시각: 2026-07-29
- 스캔 결과: **7/7 서비스 기동 정상**, 총 76개 경로 등록 확인

---

## 요약 판정

| 계약 항목 | 계약 정의 (00_SHARED_CONTRACT) | 실제 구현 | 판정 |
|---|---|---|---|
| URL 프리픽스 | 모든 URL `/api/v1/...` | 7개 서비스 전부 `/api/v1/*` 사용 | ✅ 일치 |
| 응답 봉투 | `{"data": ..., "error": null}` | 전부 동일 봉투 반환 (실측 확인) | ✅ 일치 |
| 헬스체크 | `GET /healthz` → `{"status":"ok"}` | 7/7 등록 | ✅ 일치 |
| 메트릭 | `GET /metrics` | 7/7 등록 | ✅ 일치 |
| 포트 배정 | 8001~8007 | 표와 100% 동일 | ✅ 일치 |
| 이벤트 카탈로그 | 18종 이벤트명 | 18/18 `contracts.events.EventType`로 구현 | ✅ 일치 |
| 이벤트 **전달** | 발행자 → 구독자 도달 | **도달 0건** (아래 M-8) | ❌ 불일치 |

> **결론: 서비스 구현체는 계약을 어기지 않았습니다.**
> 404의 원인은 서비스가 아니라 **호출자(`tools/test_console.py`)가 계약을 어긴 것**입니다.
> 계약은 `/api/v1` 프리픽스를 요구하는데, 콘솔은 프리픽스 없이 호출하고 있었습니다.

---

## M-1 ~ M-4: 404의 직접 원인 — `tools/test_console.py` 프리픽스 누락

계약 §5는 "모든 URL: `/api/v1/...` 프리픽스"를 규정합니다. 콘솔의 4개 호출 전부가 이를 누락했습니다.

| # | 콘솔이 호출한 경로 | 실제 등록된 경로 | 결과 |
|---|---|---|---|
| M-1 | `POST :8001/versions/register` | `POST :8001/api/v1/versions/register` | 404 |
| M-2 | `POST :8002/distribute/plan` | `POST :8002/api/v1/distribute/plan` | 404 |
| M-3 | `POST :8004/schedules/generate` | `POST :8004/api/v1/schedules/generate` | 404 |
| M-4 | `GET :8007/audit/timeline` | `GET :8007/api/v1/audit/timeline` | 404 |

---

## M-5 ~ M-7: 프리픽스만 고쳐도 **여전히 실패**하는 페이로드 불일치

이 3건은 프리픽스 수정만으로는 404가 422로 바뀔 뿐입니다. 실제 스키마를 `/openapi.json`에서 추출해 대조한 결과입니다.

### M-5. `versions/register` — 콘텐츠 타입 자체가 다름
- **콘솔**: `json={"round_id": ..., "source": "test_console"}`
- **실제**: `multipart/form-data`
  - 필수: `file`(바이너리), `round_id`, `kind`, `actor` / 선택: `team_name`
- **영향**: JSON 전송 시 422. `source` 필드는 스키마에 아예 없고, 필수 `file`/`kind`/`actor`가 누락됨.

### M-6. `distribute/plan` — 필수 필드 `master_version_id` 누락
- **콘솔**: `{"round_id": ...}`
- **실제 필수**: `round_id`, `master_version_id`
- **영향**: 422. `master_version_id`는 M-5 응답의 `data.version_id`로만 얻을 수 있음 → **선행 호출 없이는 단독 호출 불가**.

### M-7. `schedules/generate` — 필수 필드 `plan_id` 누락
- **콘솔**: `{"round_id": ...}`
- **실제 필수**: `round_id`, `plan_id`
- **영향**: 422. `plan_id`는 M-6 응답의 `data.plan_id`에서만 획득 가능.

> **핵심**: 이 4개 호출은 서로 독립이 아니라 **ID를 물고 넘어가는 체인**입니다.
> 기존 콘솔은 4개를 독립 호출로 작성해 두어, 경로를 고쳐도 구조적으로 성공할 수 없었습니다.
> `register → version_id → plan → plan_id → approve → generate` 순서가 강제됩니다.

---

## M-8. 이벤트 버스 미연결 — 타임라인/KPI가 항상 비어 있는 원인

실측: 전체 체인 성공(201/200/201/200/201) 후에도
`GET /api/v1/audit/timeline` → `{"data": [], "error": null}`,
`GET /api/v1/dashboard/kpi` → 전 항목 0.

원인은 **독립적인 2가지**이며, 둘 다 고쳐야 이벤트가 흐릅니다.

**(a) 전송 계층 — 프로세스별 인메모리 버스**
7개 서비스 전부 `REDIS_URL` 기본값이 `fakeredis://` 입니다.

| 서비스 | 설정 위치 | 기본값 |
|---|---|---|
| 01~07 | `services/*/app/config.py` | `os.getenv("REDIS_URL", "fakeredis://")` |

`fakeredis`는 **해당 uvicorn 프로세스 메모리 안에서만** 동작합니다. 서비스마다 창을 따로 띄웠으므로
FakeRedis 인스턴스도 7개가 따로 존재 → 01이 발행한 이벤트는 07에 **원천적으로 도달 불가**.

**(b) 채널 명명 규칙 3종 충돌**
전송 계층을 실 Redis로 바꿔도, 채널 이름이 서로 달라 여전히 도달하지 않습니다.

| 서비스 | 발행/구독 채널 | 근거 |
|---|---|---|
| 01 version-manager | `"hr-events"` (고정) | `app/infrastructure/event_bus.py` `_CHANNEL` |
| 02 distributor | `event_type` 그대로 (예: `DISTRIBUTION_APPROVED`) | `app/infrastructure/event_bus.py` `publish()` |
| 07 audit-analytics | `psubscribe("hr.*")` | `app/config.py` `event_channel_pattern` |

07이 기다리는 `hr.*` 패턴은 `hr-events`(하이픈)에도, `DISTRIBUTION_APPROVED`에도 매칭되지 않습니다.

**우회 경로 (구현되어 있음)**: 07은 `POST /api/v1/audit/events`로 표준 봉투를
HTTP 직접 수신할 수 있습니다(단건/배열 모두 허용). 버스 수정 전까지 이 경로로 타임라인 검증이 가능합니다.

---

## M-9. `test_console.py` 타임라인 탭 — 응답 키 오독

- **콘솔**: `r.json().get("events", [])`
- **실제 봉투**: `{"data": [...], "error": null}` (계약 §5)
- **영향**: HTTP 200이어도 항상 빈 목록으로 표시됨. 경로를 고쳐도 화면은 계속 "이벤트가 없습니다".

---

## M-10 ~ M-11: `tools/test_runner.py` 불일치

러너는 프리픽스는 올바르나(`/api/v1/*`), 다음 2건이 어긋납니다.

### M-10. `scenario_happy_path` — `plan_id` 미전달 (M-7과 동일 원인)
- 러너는 `plan_id`를 받아두고도 `schedules/generate`에 `{"round_id", "algorithm"}`만 전송 → 422.
- 추가로 `algorithm: "v4_hierarchical"`을 보내지만 서비스 기본값은 `"v5"`입니다.

### M-11. `scenario_noshow` — 존재하지 않는 목록 엔드포인트 호출
- **러너**: `GET :8004/api/v1/schedules?round_id=...`
- **실제**: 스케줄러에 컬렉션 조회 경로가 **없음**. `/api/v1/schedules/{schedule_id}` 단건 조회만 등록됨.
- **영향**: 404 → `schedule_id` 자동 조회 실패 → 시나리오 중단.

---

## 조치 우선순위

| 순위 | 항목 | 조치 | 범위 |
|---|---|---|---|
| P0 | M-1~M-7 | 콘솔을 체인 방식 + 실제 스키마로 재작성 | STEP 3 |
| P0 | M-9 | `.get("events")` → `.get("data")` | STEP 3 |
| P1 | M-10, M-11 | 러너 `plan_id` 전달, 존재하는 경로로 교체 | STEP 3 |
| P2 | M-8 | 실 Redis 전환 + 채널 규칙 통일 (별도 작업) | 범위 외 |
