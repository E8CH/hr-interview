# 🚀 HR 면접일정 자동생성 시스템 — BMAD 명세 세트

> 7개 마이크로서비스를 Claude Code 창 7개로 **동시 병렬 구현**하기 위한 명세 세트입니다.

---

## 📁 파일 구성

| 파일 | 담당 | 병렬 창 | 우선순위 |
|---|---|---|---|
| **00_SHARED_CONTRACT.md** | 공통 계약 (이벤트/타입/포트) | — | 🔴 최우선 (모두 참조) |
| 01_version_manager.md | 자료취합 버전 관리 | 창 1 | 🟢 즉시 시작 |
| 02_distributor.md | 지원자 배포 엔진 | 창 2 | 🟢 즉시 시작 |
| 03_response_collector.md | 회신 수집 · 리마인더 | 창 3 | 🟢 즉시 시작 |
| 04_scheduler.md | 면접 일정 배치 | 창 4 | 🟢 즉시 시작 |
| 05_repair_engine.md | 재편성 · 노쇼 대응 | 창 5 | 🟢 즉시 시작 |
| 06_notification_hub.md | 통합 알림 | 창 6 | 🟢 즉시 시작 |
| 07_audit_analytics.md | 감사 · 대시보드 | 창 7 | 🟢 즉시 시작 |

---

## 🎯 병렬 개발 전략

### 개발 순서 원칙
1. **00_SHARED_CONTRACT.md는 시작 전 확정**. 이 문서만 확정되면 나머지 7개는 완전 독립.
2. 각 창에서 서비스 하나씩 담당. 다른 서비스와 통신은 **모두 mock 처리**.
3. 통합 테스트 단계에서 실제 서비스 간 연동.

### Claude Code 창 배정 예시
```
창 1 → 01_version_manager.md      → SHA-256, 무결성 검증
창 2 → 02_distributor.md          → 팀 프로필, 스코어링
창 3 → 03_response_collector.md   → 웹폼, 3단계 리마인더
창 4 → 04_scheduler.md            → v1/v4/v5 알고리즘
창 5 → 05_repair_engine.md        → 안전 재편성, Plan A/B/C
창 6 → 06_notification_hub.md     → 이메일/Slack/SMS 통합
창 7 → 07_audit_analytics.md      → 이벤트 수집, 대시보드
```

### 각 창 시작 프롬프트 (예시)
```
너는 Service 01 (Version Manager)를 담당한다.
- 명세: bmad/01_version_manager.md
- 공통 계약: bmad/00_SHARED_CONTRACT.md (반드시 준수)
- 다른 서비스는 mock으로 처리 (Redis Pub/Sub 이벤트 발행만 실제로)
- 완료 판정 체크리스트를 모두 통과할 때까지 반복 구현
```

---

## 🔗 서비스 간 통신 요약

```
Service 01 (Version)
    │ MASTER_REGISTERED
    ▼
Service 02 (Distributor)
    │ DISTRIBUTION_APPROVED
    ├──────────────┐
    ▼              ▼
Service 03      Service 06 (Notify)
(Response)      │
    │ RESPONSE_RECEIVED
    ▼
Service 04 (Scheduler)
    │ SCHEDULE_LOCKED
    ├──────────────┐
    ▼              ▼
Service 05      Service 06
(Repair)        │
    │ REPAIR_EXECUTED
    ▼
Service 06 (Notify)

전체 이벤트를 Service 07 (Audit) 이 상시 수집
```

---

## ✅ 통합 완료 판정

7개 서비스가 모두 개별 완료 판정을 통과한 뒤, 다음 통합 시나리오가 성공해야 합니다.

**End-to-End 통합 테스트 시나리오**
1. HR이 마스터 엑셀 업로드 → Service 01 등록
2. 무결성 검증 통과 확인
3. Service 02가 자동으로 5개 팀 배포안 생성
4. HR 승인 → Service 03이 면접위원에게 자동 요청 발송
5. 면접위원 응답 시뮬레이션 → Service 04 스케줄 생성
6. 4대 규칙 준수율 85% 이상 확인
7. 스케줄 LOCKED → Service 06이 지원자·면접관 안내 발송
8. 노쇼 시뮬레이션 → Service 05가 Plan A/B/C 제시
9. HR이 Plan A 선택 → 재편성 완료
10. Service 07 대시보드에서 전체 회차 종합 리포트 확인

**성공 기준**
- 전체 프로세스 소요시간 5분 이내 (기존 16~48시간)
- 하드 제약 위반 0건
- 4대 규칙 준수율 85% 이상
- 노쇼 재예약률 100% (하드 위반 없이)

---

## 📋 지금까지 검증된 근거

각 명세는 다음 프로토타입 코드에서 이미 검증된 로직을 서비스화합니다.

| 서비스 | 근거 코드 | 검증 결과 |
|---|---|---|
| 01 | `version_manager.py` | 6개 파일 등록·중복 6건 자동 감지 |
| 02 | `distributor.py` + `HR_후보자_팀배포_업무프로세스.md` | 팀별 정원 100% 정확 |
| 03 | `response_engine.py` | 회신 시간 -60%, 완료율 +6%p |
| 04 | `scheduler_v1.py` + `scheduler_v4_hierarchical.py` | 4대 규칙 준수율 90% |
| 05 | `scheduler_v3_1_safe_repair.py` | 하드 위반 0건, Plan A/B/C 정상 |
| 06 | `response_engine.py`의 리마인더 로직 | 3단계 스케줄 정확도 검증 |
| 07 | v2 대시보드 위젯 로직 | KPI·온도계·위험 신호 검증 |
