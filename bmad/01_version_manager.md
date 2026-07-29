# 📦 Service 01 — Version Manager

> **필수 참조**: `00_SHARED_CONTRACT.md` (공통 이벤트/타입/포트)
> **담당 단계**: 1️⃣ 자료취합 버전 관리
> **포트**: 8001 · **DB 스키마**: `version_db`

---

## B — Business Context (왜 만드는가)

### 해결하는 문제
- 취합파일과 팀별 배포본이 매 회차 반복 생성되면서 **어느 파일이 최신인지 담당자만 아는 속인화** 발생
- 마스터-배포본 간 지원자ID 무결성이 사람 눈으로 검증됨 → 중복 배포·누락 발생
- 파일 수정 이력이 파일명(v1_최종_진짜최종.xlsx)에만 남아 감사 불가

### 비즈니스 가치
- 파일 등록 즉시 SHA-256 지문 계산 → 위·변조 자동 감지
- 회차별 무결성 검증 자동화 → 중복 배포 5건, 미배포 384명 같은 이슈를 몇 초 만에 탐지
- append-only 로그로 모든 이력 보관 → 인수인계 가능

### 성공 기준
- 파일 등록 API 응답 500ms 이내
- 회차 무결성 검증 5초 이내
- 롤백 시 100% 이전 상태 복원

---

## M — Model (데이터 모델)

### 테이블 스키마

```sql
CREATE TABLE versions (
    version_id      VARCHAR(64) PRIMARY KEY,
    round_id        VARCHAR(32) NOT NULL,
    kind            VARCHAR(32) NOT NULL,  -- 'master' | 'team_distribution'
    team_name       VARCHAR(64),           -- kind=team_distribution 때만
    file_name       VARCHAR(255) NOT NULL,
    file_path       TEXT NOT NULL,         -- MinIO 경로
    fingerprint     VARCHAR(16) NOT NULL,  -- SHA-256 앞 16자
    applicant_count INTEGER NOT NULL,
    applicant_ids   TEXT[],                -- PostgreSQL array
    actor           VARCHAR(64) NOT NULL,
    parent_version  VARCHAR(64),           -- 롤백 시 이전 버전 참조
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE   -- 롤백 시 이전 것 false
);
CREATE INDEX idx_versions_round ON versions(round_id, kind, team_name);
CREATE INDEX idx_versions_fp ON versions(fingerprint);

CREATE TABLE integrity_checks (
    check_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id        VARCHAR(32) NOT NULL,
    checked_at      TIMESTAMPTZ DEFAULT NOW(),
    status          VARCHAR(16) NOT NULL,  -- 'OK' | 'ISSUES_FOUND' | 'NO_MASTER'
    master_count    INTEGER,
    distributed_count INTEGER,
    undistributed_count INTEGER,
    duplicate_count INTEGER,
    issues          JSONB
);
```

### 도메인 규칙
- `fingerprint` 계산: `hashlib.sha256(file_bytes).hexdigest()[:16]`
- 같은 `(round_id, kind, team_name)` 재등록 시 이전 레코드 `is_active=false`, 신규 레코드가 참조
- `is_active=true`인 것이 항상 최신

---

## A — API (엔드포인트)

### 등록
```
POST /api/v1/versions/register
Content-Type: multipart/form-data

body:
  file: <binary>
  round_id: "R2026-Q3-01"
  kind: "master" | "team_distribution"
  team_name: "AI솔루션팀"   (kind=team_distribution 필수)
  actor: "HR김민지"

response 201:
{
  "data": {
    "version_id": "vm_abc123",
    "fingerprint": "6d628110544ea598",
    "applicant_count": 467,
    "created_at": "2026-07-29T10:00:00Z"
  }
}
```

### 최신 버전 조회
```
GET /api/v1/versions/{round_id}?kind=master&team_name=AI솔루션팀

response 200:
{
  "data": {
    "version_id": "vm_abc123",
    "fingerprint": "6d628110544ea598",
    "applicant_count": 16,
    "actor": "HR김민지",
    "created_at": "2026-07-29T10:00:00Z"
  }
}
```

### 무결성 검증
```
POST /api/v1/versions/verify/{round_id}

response 200:
{
  "data": {
    "status": "ISSUES_FOUND",
    "master_count": 467,
    "distributed_count": 88,
    "undistributed_count": 384,
    "duplicate_count": 5,
    "issues": [
      {"type": "DUPLICATE_DISTRIBUTION", "applicant_id": "3672536",
       "teams": ["AI솔루션팀", "로봇응용기술팀"]}
    ]
  }
}
```

### 이력 조회
```
GET /api/v1/versions/{round_id}/history

response 200:
{
  "data": [
    {"version_id": "vm_abc124", "kind": "team_distribution",
     "team_name": "AI솔루션팀", "is_active": true, ...},
    ...
  ]
}
```

### 롤백
```
POST /api/v1/versions/rollback
body: {"version_id": "vm_prev123"}

response 200:
{"data": {"restored_version_id": "vm_prev123"}}
```

### Diff
```
GET /api/v1/versions/diff?from=vm_abc123&to=vm_abc124

response 200:
{
  "data": {
    "added_ids": ["3859176"],
    "removed_ids": [],
    "unchanged_count": 15
  }
}
```

---

## D — Design (구현 설계)

### 이벤트 발행
- `MASTER_REGISTERED`: 마스터 파일 등록 성공 시
- `DISTRIBUTION_REGISTERED`: 팀별 배포본 등록 성공 시
- `INTEGRITY_VIOLATED`: 검증 결과 status=ISSUES_FOUND일 때

### 구독 이벤트
없음 (진입점 서비스)

### 핵심 로직 참고
- 이미 검증된 프로토타입 `version_manager.py` 로직을 서비스화
- 지문 계산, 지원자ID 추출, 무결성 검증 함수는 그대로 재사용

### 의존 서비스
- MinIO (파일 저장)
- Redis (이벤트 발행)
- PostgreSQL (versions, integrity_checks)

### 프로젝트 구조
```
version-manager/
├── app/
│   ├── main.py
│   ├── api/versions.py
│   ├── domain/version.py
│   ├── infrastructure/
│   │   ├── db.py
│   │   ├── minio_client.py
│   │   └── event_bus.py
│   ├── services/
│   │   ├── fingerprint.py
│   │   ├── integrity_checker.py
│   │   └── excel_parser.py    # 실제 엑셀에서 지원자ID 추출
│   └── events.py
├── tests/
│   ├── test_fingerprint.py
│   ├── test_integrity.py
│   └── test_api.py
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

### 테스트 요구사항
- `test_fingerprint`: 동일 파일 → 동일 지문
- `test_integrity_no_master`: 마스터 미등록 시 status=NO_MASTER
- `test_integrity_duplicate`: 두 팀 파일에 동일 ID 존재 시 duplicate_count 증가
- `test_rollback`: 롤백 후 이전 버전이 is_active=true

### 완료 판정 체크리스트
- [ ] `docker-compose up`으로 로컬 실행
- [ ] 6개 실제 엑셀 파일(master + 5개 팀)을 등록 후 무결성 검증 실행 → 중복 6건, 미배포 384명 자동 감지
- [ ] pytest 통과율 70% 이상
- [ ] OpenAPI 스펙 `/docs`에서 열람 가능
- [ ] `MASTER_REGISTERED` 이벤트가 Redis에 실제로 발행됨을 로그로 확인
