# 📦 Service 02 — Distributor

> **필수 참조**: `00_SHARED_CONTRACT.md`
> **담당 단계**: 2️⃣ 지원자 명단 정리·전달
> **포트**: 8002 · **DB 스키마**: `dist_db`

---

## B — Business Context

### 해결하는 문제
- HR 담당자가 취합파일 467명에서 팀별로 나누는 기준이 6개 축(R&D · 조직 · 직무 · 전공 · 학위비율 · 특수태그) 조합의 암묵지
- 배포 결과에 “왜 이 팀에 갔는지” 사유가 파일에 남지 않음
- 중복 배포(5건)가 의도인지 실수인지 구분 불가

### 비즈니스 가치
- 팀 프로필 JSON 기반으로 자동 배포 → 담당자 재량 없이도 재현 가능
- 각 배정에 사유 태그 부착 → 완전한 설명 가능
- 정원 100% 정확도 확보 (실측 검증됨)
- 개별 지원자 재현율은 5.7% 수준이므로 **HR 검수 프로세스와 결합** 필수

### 성공 기준
- 마스터 467명 입력 → 5개 팀 배포안 생성 3초 이내
- 팀별 정원 편차 ±0명
- 모든 배정에 태그 최소 2개 부여

---

## M — Model

### 테이블 스키마

```sql
CREATE TABLE team_profiles (
    team_name           VARCHAR(64) PRIMARY KEY,
    primary_job         TEXT[],
    secondary_job       TEXT[],
    preferred_majors    TEXT[],
    org_allowed         TEXT[],
    grad_ratio_target   FLOAT DEFAULT 0.30,
    target_headcount    INTEGER NOT NULL,
    special_tags        TEXT[],
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE distribution_plans (
    plan_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    round_id            VARCHAR(32) NOT NULL,
    status              VARCHAR(16) NOT NULL,  -- 'draft'|'approved'|'rejected'|'adjusted'
    master_version_id   VARCHAR(64) NOT NULL,  -- Service 01의 version_id
    total_applicants    INTEGER,
    created_by          VARCHAR(64),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    approved_at         TIMESTAMPTZ,
    approved_by         VARCHAR(64)
);

CREATE TABLE assignment_reasons (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id             UUID REFERENCES distribution_plans(plan_id),
    applicant_id        VARCHAR(32) NOT NULL,
    team_name           VARCHAR(64) NOT NULL,
    score               FLOAT,
    tags                TEXT[],
    is_duplicate        BOOLEAN DEFAULT FALSE,
    primary_team        VARCHAR(64),           -- 중복 배포 시 주관 팀
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_reasons_plan ON assignment_reasons(plan_id, team_name);
```

### 초기 데이터 (5개 팀 프로필)
이 서비스는 마이그레이션 실행 시 아래 5개 팀 프로필을 자동 삽입해야 함.

```python
TEAM_PROFILES = [
    ("AI솔루션팀", ["직무다"], ["직무나","직무라"],
     ["벼리재료학과","미르지능학과","빛솔전산학부"],
     ["제1기술원"], 0.30, 16, []),
    ("로봇응용기술팀", ["직무나"], ["직무가"],
     ["빛솔전산학부","자람정보학과","가온제1공학과"],
     ["제1기술원","제2사업부"], 0.20, 19, ["타겟랩","지도교수"]),
    ("미래혁신팀", [], ["직무나","직무라","직무가"],
     ["미르지능학과","여울생산학과","가온제1공학과"],
     ["제1기술원","제2사업부"], 0.25, 17, []),
    ("배터리기술팀", ["직무가","직무라"], ["직무다"],
     ["윤슬고분자학과","나래제1공학부","여울생산학과"],
     ["제1기술원","제2사업부"], 0.20, 16, []),
    ("전극기술팀", ["직무나"], ["직무가"],
     ["해오름설계학과","온누리연산학부","한별나노학과"],
     ["제1기술원","제2사업부"], 0.35, 20, []),
]
```

---

## A — API

### 배포안 생성
```
POST /api/v1/distribute/plan
body:
{
  "round_id": "R2026-Q3-01",
  "master_version_id": "vm_abc123",
  "allow_duplicate": true,
  "duplicate_score_threshold": 0.8
}

response 201:
{
  "data": {
    "plan_id": "uuid",
    "status": "draft",
    "team_counts": {
      "AI솔루션팀": 16, "로봇응용기술팀": 19, ...
    },
    "total_applicants": 88,
    "duplicate_count": 5
  }
}
```

### 배포안 조회
```
GET /api/v1/distribute/{plan_id}

response 200:
{
  "data": {
    "plan_id": "uuid",
    "status": "draft",
    "teams": {
      "AI솔루션팀": [
        {"applicant_id": "3339449", "score": 8.5,
         "tags": ["PRIMARY_JOB", "PREFERRED_MAJOR", "ORG_MAIN"]}
      ]
    }
  }
}
```

### 승인 · 조정 · 반려
```
POST /api/v1/distribute/{plan_id}/approve       body: {"actor": "HR김민지"}
POST /api/v1/distribute/{plan_id}/adjust        body: {"moves": [{"applicant_id":"...", "from":"AI솔루션팀", "to":"로봇응용기술팀", "reason":"..."}]}
POST /api/v1/distribute/{plan_id}/reject        body: {"reason": "..."}
```

### 팀 프로필 관리
```
GET  /api/v1/profiles
GET  /api/v1/profiles/{team_name}
PUT  /api/v1/profiles/{team_name}    body: {팀 프로필 전체}
```

### 팀별 엑셀 내보내기
```
GET /api/v1/distribute/{plan_id}/export/{team_name}

response 200:
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
(파일 다운로드)
```

---

## D — Design

### 이벤트 발행
- `DISTRIBUTION_PLAN_CREATED`: 배포안 생성 완료
- `DISTRIBUTION_APPROVED`: HR 승인 완료
- `DISTRIBUTION_ADJUSTED`: HR 수동 조정 반영

### 구독 이벤트
- `MASTER_REGISTERED`: (선택) 자동 배포안 생성 트리거
- `INTEGRITY_VIOLATED`: 배포 중단 알림

### 스코어링 알고리즘
```python
def score_candidate(candidate, profile) -> tuple[float, list[str]]:
    score, tags = 0, []
    if candidate.job_1st in profile.primary_job:
        score += 5; tags.append("PRIMARY_JOB")
    elif candidate.job_1st in profile.secondary_job:
        score += 2; tags.append("SECONDARY_JOB")
    if candidate.team_1st == "제1기술원":
        score += 1; tags.append("ORG_MAIN")
    elif candidate.team_1st in profile.org_allowed:
        score += 0.5; tags.append("ORG_ALT_QUOTA")
    else:
        return -100, []
    majors = {candidate.major_final, candidate.major_bachelor} - {None}
    if majors & set(profile.preferred_majors):
        score += 3; tags.append("PREFERRED_MAJOR")
    if candidate.target_lab == "Y" and "타겟랩" in profile.special_tags:
        score += 10; tags.append("TARGET_LAB")
    if candidate.advisor and "지도교수" in profile.special_tags:
        score += 5; tags.append("ADVISOR_ROUTE")
    return score, tags
```

### 배포 파이프라인
1. Service 01에서 마스터 파일 다운로드 (`master_version_id` → 지원자 데이터 로드)
2. `1차서류결과=결과P` AND `R&D=구분R` 필터
3. 각 지원자 × 5팀 스코어 계산
4. 스코어 상위 순 정원 채우기, 정원 초과 시 2위 팀 (`OVERFLOW_REASSIGN` 태그)
5. 스코어 1위와 2위가 80% 이상이면 중복 배포 (`DUPLICATE_REVIEW` 태그)
6. `assignment_reasons` 저장 → `DISTRIBUTION_PLAN_CREATED` 발행

### 프로젝트 구조
```
distributor/
├── app/
│   ├── main.py
│   ├── api/{plans.py, profiles.py, export.py}
│   ├── domain/{profile.py, plan.py}
│   ├── services/{scorer.py, distributor_engine.py, excel_exporter.py}
│   ├── infrastructure/{db.py, version_client.py, event_bus.py}
│   └── events.py
```

### 테스트 요구사항
- `test_scorer`: 같은 후보라도 팀 프로필에 따라 다른 점수
- `test_capacity`: 정원 초과 시 다음 팀으로
- `test_duplicate`: 임계값 이상 시 두 팀에 배포
- `test_backtest`: 실제 88명 데이터로 팀별 인원수 100% 재현

### 완료 판정 체크리스트
- [ ] 마스터 파일 입력 → 5개 팀 배포안 3초 이내 생성
- [ ] 팀별 정원 오차 0
- [ ] 모든 assignment에 최소 2개 태그
- [ ] `DISTRIBUTION_APPROVED` 이벤트 발행 후 Service 03·06이 수신 가능
- [ ] `/docs` OpenAPI 스펙 노출
