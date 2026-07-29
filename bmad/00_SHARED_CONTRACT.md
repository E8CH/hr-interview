# 🤝 공통 계약 (Shared Contract) — 모든 서비스 필수 참조

> 이 문서는 7개 마이크로서비스가 **동시 병렬 구현되어도 서로 어긋나지 않도록** 하는 최소 공통 규약입니다.
> 각 서비스 명세(01~07)는 이 문서를 참조합니다. 이 문서만 확정되면 나머지는 독립 개발 가능합니다.

---

## 1. 공통 기술 스택

| 항목 | 값 | 비고 |
|---|---|---|
| 언어 | Python 3.11+ | |
| 웹 프레임워크 | FastAPI 0.115+ | Uvicorn 실행 |
| ORM | SQLAlchemy 2.x | |
| DB | PostgreSQL 15 (서비스별 스키마 분리) | PoC는 SQLite 허용 |
| 이벤트 버스 | Redis Pub/Sub | PoC. 프로덕션은 Kafka |
| 파일 저장 | MinIO (S3 호환) | PoC는 로컬 디렉토리 허용 |
| 인증 | JWT (Bearer Token) | HS256, 만료 8h |
| 로깅 | structlog (JSON 출력) | |
| 테스트 | pytest + httpx | 커버리지 70% 이상 |
| 컨테이너 | Docker + docker-compose | |

---

## 2. 서비스 목록 & 포트 배정

| # | 서비스 | 포트 | 담당 단계 | 소유 DB 스키마 |
|---|---|---|---|---|
| 01 | version-manager | 8001 | 1️⃣ | `version_db` |
| 02 | distributor | 8002 | 2️⃣ | `dist_db` |
| 03 | response-collector | 8003 | 3️⃣ | `resp_db` |
| 04 | scheduler | 8004 | 4️⃣ | `sched_db` |
| 05 | repair-engine | 8005 | 5️⃣ | `repair_db` |
| 06 | notification-hub | 8006 | 공통 | `notif_db` |
| 07 | audit-analytics | 8007 | 공통 | `audit_db` |

---

## 3. 표준 도메인 타입 (모든 서비스 공유)

```python
# shared/types.py — 각 서비스가 vendored 복사본 유지
from typing import Literal, TypedDict
from datetime import datetime

RoundId = str        # "R2026-Q3-01" 형식
ApplicantId = str    # 마스터 엑셀의 "지원자 번호"
InterviewerId = str  # "IV001" 형식
TeamName = Literal["AI솔루션팀", "로봇응용기술팀", "미래혁신팀",
                   "배터리기술팀", "전극기술팀"]
Day = Literal["월", "화", "수", "목", "금"]
Hour = Literal["09시", "10시", "11시", "14시", "15시", "16시"]
Degree = Literal["학사", "대학원"]
LockLevel = Literal["DRAFT", "CONFIRMED", "LOCKED"]
```

---

## 4. 이벤트 스키마 표준

모든 이벤트는 이 봉투(envelope)를 따릅니다.

```json
{
  "event_id": "uuid4",
  "event_type": "MASTER_REGISTERED",
  "timestamp": "2026-07-29T10:00:00Z",
  "round_id": "R2026-Q3-01",
  "producer": "version-manager",
  "correlation_id": "uuid4 (같은 회차의 이벤트 체인 추적용)",
  "payload": { /* 이벤트별 상세 */ }
}
```

### 전체 이벤트 카탈로그

| 이벤트 | 발행자 | 주 구독자 |
|---|---|---|
| `MASTER_REGISTERED` | 01 | 02, 07 |
| `DISTRIBUTION_REGISTERED` | 01 | 07 |
| `INTEGRITY_VIOLATED` | 01 | 02, 07 |
| `DISTRIBUTION_PLAN_CREATED` | 02 | 07 |
| `DISTRIBUTION_APPROVED` | 02 | 03, 06, 07 |
| `DISTRIBUTION_ADJUSTED` | 02 | 07 |
| `REQUEST_SENT` | 03 | 06, 07 |
| `RESPONSE_RECEIVED` | 03 | 04, 07 |
| `REMINDER_SENT` | 03 | 06, 07 |
| `NON_RESPONDER_ESCALATED` | 03 | 06, 07 |
| `SCHEDULE_GENERATED` | 04 | 07 |
| `SCHEDULE_LOCKED` | 04 | 05, 06, 07 |
| `RULE_VIOLATED` | 04 | 07 |
| `NOSHOW_REPORTED` | 05 (외부 유입) | — |
| `REPAIR_EXECUTED` | 05 | 06, 07 |
| `PARTICIPANT_DEFERRED` | 05 | 06, 07 |
| `NOTIFICATION_SENT` | 06 | 07 |
| `NOTIFICATION_FAILED` | 06 | 07 |

---

## 5. 공통 REST 규약

- 모든 URL: `/api/v1/...` 프리픽스
- 응답 스키마: `{"data": ..., "error": null}` 또는 `{"data": null, "error": {"code": "...", "message": "..."}}`
- 에러 코드: 대문자 스네이크 (예: `VALIDATION_FAILED`, `NOT_FOUND`, `INTEGRITY_VIOLATION`)
- 헬스체크: `GET /healthz` → `{"status": "ok"}`
- 메트릭: `GET /metrics` (Prometheus 포맷)

---

## 6. 공통 데이터 모델

### Applicant (마스터 엑셀 컬럼 매핑)

```python
class Applicant(BaseModel):
    applicant_id: str            # "지원자 번호"
    name: str                    # "한글성명"
    team_1st: str                # "1지망_조직"
    job_1st: str                 # "1지망_직무"
    rnd_type: str                # "R&D/N-R&D" (구분R/구분N)
    degree_type: str             # "최종학력_학교유형" (과정1=학사, 과정2/3=대학원)
    major_final: str | None      # "최종학력_주전공"
    major_bachelor: str | None   # "학사1_주전공"
    gpa_final: float | None      # "최종학력_환산학점"
    target_lab: str | None       # "타겟랩여부"
    advisor: str | None          # "지도교수"
    prev_applications: int = 0   # "이전 지원 전체 횟수"
    doc_result: str              # "1차서류 결과" (결과P/결과F)
```

### Interviewer

```python
class Interviewer(BaseModel):
    interviewer_id: str
    name: str
    team: TeamName
    max_daily: int = 6
    priority: int = 1            # 1=리더, 2=실무
    email: str
    backup_email: str | None = None
```

### Assignment

```python
class Assignment(BaseModel):
    assignment_id: str
    applicant_id: str
    interviewer_id: str
    day: Day
    hour: Hour
    lock_level: LockLevel = "DRAFT"
    reason_tags: list[str] = []
    created_at: datetime
```

---

## 7. 공통 태그 표준 (배포 사유)

```
PRIMARY_JOB          팀 주력 직무 매칭
SECONDARY_JOB        팀 보조 직무 매칭
PREFERRED_MAJOR      팀 선호 전공 매칭
ORG_MAIN             제1기술원 배정
ORG_ALT_QUOTA        제2사업부 쿼터 배정
TARGET_LAB           타겟랩 지정 배정
ADVISOR_ROUTE        지도교수 관계 배정
GRAD_BALANCE         대학원 비율 조정용
DUPLICATE_REVIEW     복수 검토 대상
HR_MANUAL            HR 담당자 재량
```

---

## 8. 4대 배치 규칙 (PPT 원본 규칙)

- **규칙 1 (SOFT)**: 학사/대학원 요일 편중 방지 — 대학원 비율 30% ±20%p
- **규칙 2 (HARD)**: 같은 팀 동시간 중복 금지
- **규칙 3 (SOFT)**: 동일 팀 세로 연속 배치 (Webex 재입장 최소화)
- **규칙 4 (SOFT)**: 첫 타임(09시·14시)은 소규모 조 우선

---

## 9. 회차 라이프사이클

```
1. MASTER_REGISTERED           ← HR이 마스터 파일 업로드
2. DISTRIBUTION_APPROVED       ← 팀별 배포 확정
3. REQUEST_SENT (×N)           ← 면접위원에게 요청 발송
4. RESPONSE_RECEIVED (×N)      ← 응답 수집
5. SCHEDULE_LOCKED             ← 시간표 확정
6. NOTIFICATION_SENT (×N)      ← 지원자/면접관 안내
7. NOSHOW_REPORTED (×N, 옵션)  ← 노쇼 발생
8. REPAIR_EXECUTED (×N, 옵션)  ← 재편성
```

모든 이벤트는 `round_id`와 `correlation_id`를 공유하여 추적 가능해야 합니다.

---

## 10. 리포지토리 표준 구조

각 서비스는 다음 구조를 따릅니다.

```
service-name/
├── app/
│   ├── main.py              # FastAPI 앱
│   ├── api/                 # 라우터
│   ├── domain/              # 도메인 모델
│   ├── infrastructure/      # DB, 이벤트 버스 어댑터
│   ├── services/            # 비즈니스 로직
│   └── events.py            # 이벤트 발행/구독
├── tests/
├── migrations/              # Alembic
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 11. 병렬 개발자에게: 이것만은 지켜라

1. **이 문서(00)의 이벤트 스키마·도메인 타입·태그·규칙 정의는 절대 수정 금지.** 자기 서비스 내부에서 필요하면 확장 스키마를 별도로 만들되, 봉투는 유지.
2. **다른 서비스의 DB에 직접 접근 금지.** 반드시 API 또는 이벤트로만.
3. **자기 서비스가 발행하는 이벤트는 반드시 이 문서의 카탈로그에 등록된 이름을 사용.**
4. **모든 서비스는 `docker-compose up` 하나로 로컬 실행 가능해야 함.**
5. **기본 포트/DB 스키마 이름은 위 표를 따라라. 충돌 방지.**
