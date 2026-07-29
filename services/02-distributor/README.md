# Service 02 — Distributor

팀 프로필(JSON) 기반 자동 배포 엔진. 6개 축 스코어링 → 배포 사유 태그 부착 → 정원 정확 배정 →
중복 배포 관리 → 팀별 엑셀 내보내기.

- 포트: **8002**
- DB 스키마: **dist_db** (PoC는 SQLite 파일)
- BMAD 명세: `../../bmad/02_distributor.md`
- 공통 계약: `../../bmad/00_SHARED_CONTRACT.md`

## 로컬 실행 (Docker 불필요)

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8002 --reload
```

기동 시 `init_db()` 가 테이블을 만들고 5개 팀 프로필을 시딩한다.
`/docs` 에서 OpenAPI 스펙 확인.

```bash
# 배포안 생성 → 조회 → 승인 → 엑셀 내보내기
curl -X POST localhost:8002/api/v1/distribute/plan \
  -H 'Content-Type: application/json' \
  -d '{"round_id":"R2026-Q3-01","master_version_id":"vm_abc123"}'

curl localhost:8002/api/v1/distribute/{plan_id}
curl -X POST localhost:8002/api/v1/distribute/{plan_id}/approve \
  -H 'Content-Type: application/json' -d '{"actor":"HR김민지"}'
curl -OJ localhost:8002/api/v1/distribute/{plan_id}/export/AI솔루션팀
```

## 테스트

```bash
pytest        # pytest.ini에 --cov=app --cov-fail-under=70 포함
```

현재 **101 tests · 커버리지 98%**. 백테스트는 합성 데이터가 아니라
`data/취합파일.xlsx`(실제 467명)와 `tests/fixtures/희망지원자_*.xlsx`(실제 배포 결과)를 사용한다.

## 데이터

| 경로 | 내용 |
|---|---|
| `data/취합파일.xlsx` | 실제 마스터 467명 (`docs/` 사본) |
| `tests/fixtures/희망지원자_{팀}.xlsx` | 실제 배포 결과 = 백테스트 정답지 (88건 / 유니크 83명 / 중복 5건) |

`USE_MOCK=true` 라도 `MASTER_XLSX` 경로에 파일이 있으면 **실파일을 읽는다.**
없을 때만 시드 고정 합성 데이터(467명)로 폴백한다.

## 환경 변수

| 변수 | PoC 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./dist_db.sqlite` | SQLAlchemy URL |
| `REDIS_URL` | `fakeredis://` | `fakeredis://` 면 인메모리 Pub/Sub |
| `USE_MOCK` | `true` | Service 01 미호출 |
| `MASTER_XLSX` | `./data/취합파일.xlsx` | 목 모드에서 읽을 실제 취합파일. 비우면 합성 데이터 |
| `STORAGE_DIR` | `./storage` | 엑셀 출력 경로 |
| `VERSION_MANAGER_URL` | `http://localhost:8001` | `USE_MOCK=false` 일 때 사용 |
| `MOCK_SEED` | `20260729` | 합성 폴백 재현 시드 |

## 배포 파이프라인

1. `version_client` 로 마스터 로드 (실파일 467명)
2. `1차서류결과=결과P` ∧ `R&D=구분R` 필터 → 실데이터는 **467명 전원 통과**
   (467 → 88을 만드는 것은 필터가 아니라 팀 정원 합이다)
3. 467명 × 5팀 스코어 매트릭스 (`scorer.score_candidate`)
4. 점수 내림차순 탐욕 배정 → 정원 초과분은 차순위 팀 (`OVERFLOW_REASSIGN`)
   - **정원 보정**: 탐욕 배정이 막은 슬롯은 증대경로(Kuhn) 탐색으로 재배치 → 정원 오차 0
   - **학위비율 보정**: 정원을 유지한 채 1:1 스왑으로 대학원 비율 편차 축소 (`GRAD_BALANCE`)
5. 1위 대비 2위 점수 비율 ≥ `duplicate_score_threshold` 면 중복 배포 (`DUPLICATE_REVIEW`)
6. `assignment_reasons` 저장 → `DISTRIBUTION_PLAN_CREATED` 발행

### 스코어링 6축

| 축 | 반영 위치 | 가중치 |
|---|---|---|
| R&D | 사전 필터 (`구분R`) | — |
| 직무 | `PRIMARY_JOB` / `SECONDARY_JOB` | +5 / +2 |
| 조직 | `ORG_MAIN` / `ORG_ALT_QUOTA` / 불충족 시 배정 불가 | +1 / +0.5 / -100 |
| 전공 | `PREFERRED_MAJOR` (최종·학사 전공 중 하나라도 일치) | +3 |
| 특수태그 | `TARGET_LAB` / `ADVISOR_ROUTE` | +10 / +5 |
| 학위비율 | 배정 단계 스왑 (`GRAD_BALANCE`) — 규칙1 ±20%p | soft |

모든 배정에는 **최소 2개 태그**가 보장된다 (부족 시 `HR_MANUAL` 로 보강).

## 이벤트

| 방향 | 이벤트 | 비고 |
|---|---|---|
| 발행 | `DISTRIBUTION_PLAN_CREATED` | 배포안 생성 |
| 발행 | `DISTRIBUTION_APPROVED` | 승인 (Service 03·06 구독) |
| 발행 | `DISTRIBUTION_ADJUSTED` | HR 수동 조정 |
| 구독 | `MASTER_REGISTERED` | 자동 배포안 생성 트리거 |
| 구독 | `INTEGRITY_VIOLATED` | 해당 회차 배포 중단 (409 `INTEGRITY_VIOLATION`) |

봉투는 `shared/contracts/events.py` 의 `EventEnvelope` 를 그대로 사용하며,
`correlation_id` 에는 `plan_id` 를 넣어 회차 체인을 추적한다.

## 실데이터 백테스트 결과

`data/취합파일.xlsx` 467명 → `seed_profiles()` 기본 프로필로 배포한 실측값
(재현: `pytest tests/test_backtest.py`).

| 항목 | 실측 | 비고 |
|---|---|---|
| 생성 시간 | **0.021초** | 기준 3초 |
| 팀별 정원 | 16 / 19 / 17 / 16 / 20 = **88, 오차 0** | 정답지와 완전 일치 |
| 미배정 | 379명 | 467 − 88 (정원 초과분) |
| 최소 태그 | 전 건 2개 이상 | `HR_MANUAL` 보강 포함 |
| 선발 재현 (팀 무관) | 19 / 83 = **22.9%** | 정답지 유니크 83명 대비 |
| 팀까지 일치 | 10 / 88 = **11.4%** | 명세 기대치(5.7%)보다 높음 |
| 중복 배포 | 20건 (정답지 5건) | 아래 참고 |

- **중복 20건은 임계값 문제가 아니다.** 0.7 / 0.8 / 0.85 / 0.9 / 0.95 / 0.98 / 1.0 을 모두
  훑어봐도 20건으로 동일하다 — 플래그된 쌍이 전부 1위·2위 **정확한 동점**(비율 1.0)이기 때문이다.
  명세 규칙("1위와 2위 점수가 80% 이상")은 그대로 구현했고, 격차는 로직 결함이 아니라
  스코어 해상도(정수 가중치 +5/+3/+1)가 낮아 동점이 대량 발생하는 데이터 특성이다.
  실제 5건으로 좁히려면 GPA·이전 지원 횟수 등 타이브레이커 축을 추가해야 하며,
  이는 명세 범위 밖이라 반영하지 않았다.
- 개별 재현율이 낮은 것은 명세가 이미 전제한 바다 → **HR 검수 프로세스와의 결합이 필수**.
  `test_backtest.py` 의 재현율 단언은 목표치가 아니라 **회귀 감시 하한선**이다.
- `USE_MOCK=false` + `VERSION_MANAGER_URL` 로 전환하면 Service 01에서 마스터를 직접 받는다.

## 완료 판정 체크리스트

- [x] 마스터 파일 입력 → 5개 팀 배포안 3초 이내 생성 (실데이터 실측 0.021초)
- [x] 팀별 정원 오차 0
- [x] 모든 assignment에 최소 2개 태그
- [x] `DISTRIBUTION_APPROVED` 발행 후 Service 03·06이 채널 구독으로 수신 가능
- [x] `/docs` OpenAPI 스펙 노출
