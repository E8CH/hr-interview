# Version Manager (Service 01)

SHA-256 지문 기반 파일 버전 관리 · 마스터/팀 배포본 등록 · 회차 무결성 검증 · 롤백 · Diff.

- 포트: **8001** · DB 스키마: **version_db**
- BMAD 명세: `../../bmad/01_version_manager.md`
- 공통 계약: `../../bmad/00_SHARED_CONTRACT.md`

## PoC 모드 (Docker 불필요)

`.env` 기준으로 동작합니다.

| 항목 | 값 |
|---|---|
| DB | SQLite (`sqlite:///./version_db.sqlite`) |
| 이벤트 | FakeRedis 인메모리 (`fakeredis://`) |
| 파일 저장 | 로컬 `./storage/` |

## 실행

```bash
pip install -r requirements.txt

# 로컬 실행 (Docker 없이)
uvicorn app.main:app --port 8001 --reload
#  → OpenAPI 문서: http://127.0.0.1:8001/docs
#  → 헬스체크:     http://127.0.0.1:8001/healthz

# 테스트 + 커버리지
pytest tests/ --cov=app
```

## 완료 판정 데모

서버 기동 후, 실제 6개 엑셀(`../../docs/`)을 등록하고 무결성 검증:

```bash
python scripts/demo_verify.py
# 기대: master=467, undistributed=384, duplicate=5, status=ISSUES_FOUND → [PASS]
```

## API

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/v1/versions/register` | 마스터/팀 배포본 등록 (multipart) |
| GET  | `/api/v1/versions/{round_id}` | 최신 활성 버전 조회 (`?kind=&team_name=`) |
| POST | `/api/v1/versions/verify/{round_id}` | 회차 무결성 검증 |
| GET  | `/api/v1/versions/{round_id}/history` | 버전 이력 |
| POST | `/api/v1/versions/rollback` | 이전 버전으로 롤백 |
| GET  | `/api/v1/versions/diff?from=&to=` | 두 버전 간 지원자 ID Diff |

## 발행 이벤트

`MASTER_REGISTERED`, `DISTRIBUTION_REGISTERED`, `INTEGRITY_VIOLATED`
(공통 계약 `EventEnvelope` 봉투 · structlog JSON 로그로 발행 확인 가능)
