# 통합 리포트 (Integration Report)

- 작성일: 2026-07-29
- 대상: 7개 마이크로서비스 (port 8001~8007)
- 근거 자료: `tools/api_inventory.json`, `tools/mismatch_report.md`, `integration-tests/test_e2e_happy_path.py`

---

## 1. 결론 요약

| 항목 | 결과 |
|---|---|
| 서비스 기동 | **7/7 정상** |
| 등록된 총 엔드포인트 | **87개** |
| 404의 원인 | **서비스가 아니라 `tools/test_console.py`** — `/api/v1` 프리픽스 누락 |
| 통합 테스트 | **21/21 PASS** |
| 요구된 4개 API 호출 | **전부 201/200 성공** |
| 남은 최대 이슈 | **이벤트 버스 미연결** → 타임라인·KPI 항상 0 |

> 진단 정정: 최초 추정은 "구현 경로가 `shared/contracts`와 다르다"였으나, **실제로는 서비스 구현이 계약을 정확히 지키고 있었습니다.**
> 7개 서비스 모두 `/api/v1` 프리픽스, `{"data","error"}` 봉투, `/healthz`, `/metrics`를 계약대로 제공합니다.
> 계약을 어긴 쪽은 호출자인 Streamlit 콘솔이었습니다.

---

## 2. 서비스별 실제 엔드포인트 목록

전체 원본은 `tools/api_inventory.json`. 아래는 업무 엔드포인트만 정리한 것입니다.
(7개 서비스 모두 `GET /`, `GET /healthz`, `GET /metrics` 공통 보유 — 표에서는 생략)

### 01 version-manager :8001 (9개)
| 메서드 | 경로 |
|---|---|
| POST | `/api/v1/versions/register` ← **multipart/form-data** |
| GET | `/api/v1/versions/diff` |
| GET | `/api/v1/versions/{round_id}` |
| POST | `/api/v1/versions/verify/{round_id}` |
| GET | `/api/v1/versions/{round_id}/history` |
| POST | `/api/v1/versions/rollback` |

### 02 distributor :8002 (11개)
| 메서드 | 경로 |
|---|---|
| POST | `/api/v1/distribute/plan` |
| GET | `/api/v1/distribute/{plan_id}` |
| POST | `/api/v1/distribute/{plan_id}/approve` |
| POST | `/api/v1/distribute/{plan_id}/adjust` |
| POST | `/api/v1/distribute/{plan_id}/reject` |
| GET | `/api/v1/distribute/{plan_id}/export/{team_name}` |
| GET | `/api/v1/profiles` |
| GET, PUT | `/api/v1/profiles/{team_name}` |

### 03 response-collector :8003 (15개)
| 메서드 | 경로 |
|---|---|
| POST | `/api/v1/requests` |
| GET | `/api/v1/requests/{request_id}` |
| POST | `/api/v1/requests/{request_id}/close` |
| GET | `/api/v1/responses/{round_id}` |
| GET | `/api/v1/patterns/organizations` |
| GET | `/api/v1/patterns/organizations/{org}` |
| POST | `/api/v1/reminders/trigger` |
| POST | `/api/v1/reminders/run-cycle` |
| GET | `/api/v1/reminders/rules` |
| GET | `/api/v1/reminders/schedule/{invitee_id}` |
| GET | `/form/{token}` · POST `/form/{token}/submit` (공개 응답 폼, 프리픽스 예외) |

### 04 scheduler :8004 (13개)
| 메서드 | 경로 |
|---|---|
| POST | `/api/v1/schedules/generate` |
| GET | `/api/v1/schedules/{schedule_id}` |
| GET | `/api/v1/schedules/{schedule_id}/heatmap` |
| GET | `/api/v1/schedules/{schedule_id}/by-team` |
| POST | `/api/v1/schedules/{schedule_id}/validate` |
| POST | `/api/v1/schedules/{schedule_id}/lock` |
| GET | `/api/v1/schedules/{schedule_id}/rules` |
| GET, POST | `/api/v1/interviewers` |
| GET, PUT | `/api/v1/interviewers/{interviewer_id}` |
| GET | `/api/v1/rounds/{round_id}/readiness` |

> ⚠️ 컬렉션 조회 경로 `GET /api/v1/schedules` 는 **없습니다.** 단건 조회만 가능.

### 05 repair-engine :8005 (10개)
| 메서드 | 경로 |
|---|---|
| POST | `/api/v1/repair/noshow` |
| POST | `/api/v1/repair/cancel` |
| GET | `/api/v1/repair/plans/{event_id}` |
| POST | `/api/v1/repair/plans/{event_id}/select` |
| GET | `/api/v1/repair/locks/{schedule_id}` |
| POST | `/api/v1/repair/locks/upgrade` |
| GET | `/api/v1/repair/audit/{round_id}` |

### 06 notification-hub :8006 (17개)
| 메서드 | 경로 |
|---|---|
| POST | `/api/v1/notify/send` · `/api/v1/notify/broadcast` · `/api/v1/notify/process` |
| GET | `/api/v1/notify/history` · `/api/v1/notify/history/{recipient}` |
| GET | `/api/v1/notify/dead-letters` |
| GET | `/api/v1/notify/templates` · GET,PUT `/api/v1/notify/templates/{template_id}` |
| POST | `/api/v1/notify/templates/{template_id}/preview` |
| GET, POST | `/api/v1/notify/channels` · PUT `/api/v1/notify/channels/{channel_id}/toggle` |
| GET | `/api/v1/notify/track/open/{notification_id}.png` |
| GET | `/api/v1/notify/events` · POST `/api/v1/notify/events/inbound` |

### 07 audit-analytics :8007 (12개)
| 메서드 | 경로 |
|---|---|
| GET | `/api/v1/dashboard/kpi` (round_id 필수) |
| GET | `/api/v1/dashboard/organizations` · `/api/v1/dashboard/risks` |
| GET | `/api/v1/reports/rounds/{round_id}` · `/api/v1/reports/before-after` |
| GET | `/api/v1/audit/timeline` (round_id 필수) |
| POST | `/api/v1/audit/query` |
| POST | `/api/v1/audit/events` ← **이벤트 HTTP 직접 수집 (202 Accepted)** |
| GET | `/api/v1/audit/events/stats` |

---

## 3. 잘못된 경로 → 수정된 경로 매핑

### 3-1. `tools/test_console.py` — 404를 낸 4개 호출

| # | 수정 전 | 수정 후 | 추가 수정 |
|---|---|---|---|
| 1 | `POST :8001/versions/register` (JSON) | `POST :8001/api/v1/versions/register` | **multipart/form-data**로 전환. `{round_id, source}` → `file` + `{round_id, kind, actor}` |
| 2 | `POST :8002/distribute/plan` | `POST :8002/api/v1/distribute/plan` | 필수 `master_version_id` 추가 (1번 응답의 `data.version_id`) + 승인 호출 추가 |
| 3 | `POST :8004/schedules/generate` | `POST :8004/api/v1/schedules/generate` | 필수 `plan_id` 추가 (2번 응답의 `data.plan_id`) |
| 4 | `GET :8007/audit/timeline` | `GET :8007/api/v1/audit/timeline` | — |

**구조 변경**: 기존 콘솔은 4개를 서로 독립된 호출로 작성했으나, 실제 API는 ID를 물고 넘어가는 체인입니다.
경로만 고쳤다면 404가 422로 바뀌었을 뿐 여전히 실패했을 것입니다.

```
register ──version_id──▶ plan ──plan_id──▶ approve ──▶ generate ──▶ timeline
```

앞 단계 실패 시 뒤 단계를 건너뛰고 이유를 로그에 남기도록 했습니다.

### 3-2. `tools/test_console.py` — 타임라인 탭

| 수정 전 | 수정 후 |
|---|---|
| `r.json().get("events", [])` | `r.json().get("data") or []` (계약 §5 봉투) |

경로가 맞아도 항상 "이벤트가 없습니다"로 표시되던 원인입니다.

### 3-3. `tools/test_runner.py`

| # | 수정 전 | 수정 후 |
|---|---|---|
| 1 | `schedules/generate`에 `{round_id, algorithm:"v4_hierarchical"}` | `{round_id, plan_id, generated_by}` — 필수 `plan_id` 전달, 서비스 기본 알고리즘(`v5`) 사용 |
| 2 | `GET :8004/api/v1/schedules?round_id=` (존재하지 않음 → 404) | 제거. `schedule_id`를 직접 입력받고 `GET /api/v1/schedules/{id}`로 존재 확인 |
| 3 | 5단계에서 `schedule_id` 미출력 | `Schedule ID` 출력 → 노쇼 시나리오에 바로 사용 가능 |
| 4 | Windows cp949 콘솔에서 이모지 `UnicodeEncodeError`로 **중단** | `sys.stdout.reconfigure(encoding="utf-8")` 추가 |

> 4번은 이번 작업과 무관한 기존 결함이나, Windows 환경에서 러너가 1단계 직후 죽어 아무것도 실행할 수 없었기에 함께 고쳤습니다.

---

## 4. 통합 테스트 실행 결과

```
pytest integration-tests/test_e2e_happy_path.py -v
→ 21 passed in 65.74s
```

| # | 테스트 | 검증 내용 | 결과 |
|---|---|---|---|
| 01 | `register_master` | 마스터 등록 → **HTTP 201**, 지원자 120명 파싱 | ✅ |
| 02 | `create_distribution_plan` | 배포안 생성 → **HTTP 201**, 5개 팀 배정 | ✅ |
| 03 | `approve_plan` | 승인 → **HTTP 200**, status=approved | ✅ |
| 04 | `generate_schedule` | 스케줄 생성 → **HTTP 201**, 88명 배정, HARD 위반 0 | ✅ |
| 05 | `audit_timeline` | 타임라인 → **HTTP 200**, 봉투 형식 준수 | ✅ |
| 06 | `dashboard_kpi` | KPI → **HTTP 200** | ✅ |
| 07 | `audit_direct_ingestion_roundtrip` | 이벤트 직접 수집 → 202, 타임라인 반영 확인 | ✅ |
| 08 | `contract_healthz` ×7 | 7개 서비스 `/healthz` 200 | ✅ |
| 09 | `contract_api_v1_prefix` ×7 | 업무 경로 `/api/v1` 프리픽스 준수 | ✅ |

**요구사항이었던 4개 API 호출은 모두 201/200으로 성공**합니다 (테스트 01·02·04·05).

실측 결과값:
- 마스터 120명 등록 → 배포안 88명 배정 (5개 팀: 16/19/17/16/20) → 스케줄 88명 배정
- 규칙 준수율 **100.0%** (rule1~rule4 전부 100), HARD 위반 **0건**, 커버리지 100%

---

## 5. 남은 이슈와 다음 조치

### 🔴 P1 — 이벤트 버스 미연결 (가장 중요)

전체 체인이 성공해도 `audit/timeline`은 `[]`, `dashboard/kpi`는 전 항목 0을 반환합니다.
독립적인 **두 가지** 원인이 있고, 둘 다 고쳐야 이벤트가 흐릅니다.

**(a) 전송 계층**: 7개 서비스 전부 `REDIS_URL` 기본값이 `fakeredis://` → uvicorn 프로세스마다
별도 인메모리 인스턴스. 창을 따로 띄웠으므로 프로세스 간 전달이 **원천적으로 불가능**.

**(b) 채널 명명 3종 충돌**: 실 Redis로 바꿔도 이름이 달라 도달하지 않습니다.

| 서비스 | 채널 |
|---|---|
| 01 | `"hr-events"` 고정 |
| 02 | `event_type` 그대로 (`DISTRIBUTION_APPROVED` 등) |
| 07 | `psubscribe("hr.*")` |

**다음 조치**
1. `infra/`에 Redis 컨테이너 기동 후 7개 서비스에 `REDIS_URL=redis://localhost:6379` 주입 (`start_all.ps1` 수정)
2. 채널 규칙을 `hr.{event_type}` 하나로 통일 — 07의 `channel_for()`가 이미 이 형태이므로 **01·02를 07에 맞추는 것**이 변경량 최소
3. 통일 후 `test_05_audit_timeline`의 단언을 `len(events) > 0`으로 강화

> 참고: 이벤트 **이름**은 문제없습니다. 18종 카탈로그 전부 `contracts.events.EventType`으로 정확히 구현돼 있습니다. 깨진 것은 배관뿐입니다.

### 🟡 P2 — 확인 필요 (결함 여부 미확정)

- **120명 → 88명**: 마스터 120명 중 배포안에 88명만 배정됩니다. `1차서류 결과`(결과P/결과F) 필터로 보이며 정상 동작일 가능성이 높으나, 의도한 필터인지 확인이 필요합니다. `duplicate_count: 20`도 함께 확인 권장.
- **`verify` → `ISSUES_FOUND`**: `undistributed_count: 120`으로 나오는데, 이는 마스터만 등록하고 팀별 배포본(`kind=distribution`)을 등록하지 않았기 때문입니다. 시나리오상 예상된 결과이나, 배포본 등록 단계를 시나리오에 넣을지 결정 필요.

### 🟢 P3 — 커버리지 보강

- 노쇼 재편성(05) 및 알림(06) 경로는 이번 E2E에 미포함. `repair/noshow` → `repair/plans/{event_id}` → `select` 흐름을 별도 테스트로 추가 권장.
- `schedules/{id}/lock` (LOCKED 전이) 미검증.

---

## 6. 재현 방법

```powershell
# 1) 7개 서비스 기동
.\start_all.ps1

# 2) 실제 엔드포인트 재스캔 (api_inventory.json 갱신)
.\.venv\Scripts\python.exe -c "import json,httpx; ..."   # 또는 아래 통합 테스트로 대체

# 3) 통합 테스트
.\.venv\Scripts\python.exe -m pytest integration-tests/test_e2e_happy_path.py -v

# 4) CLI 시나리오
.\.venv\Scripts\python.exe tools\test_runner.py scenario happy

# 5) Streamlit 콘솔
.\.venv-ui\Scripts\streamlit run tools\test_console.py
```
