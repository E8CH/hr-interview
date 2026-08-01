"""PoC 목 데이터 (USE_MOCK=true)

Service 02(지원자 명단) / Service 03(면접관 가용성)을 대체한다.
난수 시드를 고정해 매 실행 동일한 데이터가 나오므로 테스트가 결정적이다.

규모 설계 근거
--------------
- 지원자 88명 / 5팀 → 팀당 17~18명, 대학원 비율 29.5% (규칙1 목표 30%에 근접)
- 규칙2(같은 팀 동시간 중복 금지)가 HARD이므로 팀별 동시 진행은 1건 →
  팀당 수용량 = 5일 × 8타임 = 40슬롯, 전체 200슬롯 ≫ 88명
- v4는 팀당 2일(16슬롯 × 5팀 = 80)로 제한 → 88명을 다 못 앉힌다
- v5는 팀당 3일(24슬롯 × 5팀 = 120) + Fallback → 커버리지 90%+
"""
from __future__ import annotations

import random

from app.domain.schemas import ApplicantIn, InterviewerIn
from app.infrastructure.contracts import BACK_HOURS, DAYS, HOURS

TEAMS = ["AI솔루션팀", "로봇응용기술팀", "미래혁신팀", "배터리기술팀", "전극기술팀"]

# 팀별 지원자 수 (합계 88) · 팀별 대학원 수 (합계 26 → 29.5%)
TEAM_APPLICANTS = {
    "AI솔루션팀": 18,
    "로봇응용기술팀": 18,
    "미래혁신팀": 18,
    "배터리기술팀": 17,
    "전극기술팀": 17,
}
TEAM_GRADS = {
    "AI솔루션팀": 6,
    "로봇응용기술팀": 5,
    "미래혁신팀": 5,
    "배터리기술팀": 5,
    "전극기술팀": 5,
}

_SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임"]
_GIVEN = ["민준", "서연", "도윤", "지우", "하은", "예준", "수아", "지호", "채원", "시우"]

INTERVIEWERS_PER_TEAM = 4  # 리더 1 + 실무 3


def normalize_degree(degree_type: str) -> str:
    """마스터 엑셀 '최종학력_학교유형' 매핑 (과정1=학사, 과정2/3=대학원)"""
    text = (degree_type or "").strip()
    if text in {"학사", "과정1"}:
        return "학사"
    if text in {"대학원", "과정2", "과정3", "석사", "박사"}:
        return "대학원"
    return "학사"


def build_applicants(round_id: str = "R2026-Q3-01", plan_id: str = "mock-plan") -> list[ApplicantIn]:
    """확정 명단 88명 생성 (팀·학위 분포 고정, 세부 값만 시드 난수)"""
    rng = random.Random(f"{round_id}:{plan_id}:applicants")
    applicants: list[ApplicantIn] = []
    serial = 3339000

    for team in TEAMS:
        total = TEAM_APPLICANTS[team]
        grads = TEAM_GRADS[team]
        for idx in range(total):
            serial += 1
            is_grad = idx < grads
            degree = "대학원" if is_grad else "학사"
            gpa = round(rng.uniform(3.2, 4.4), 2)
            # 대학원 지원자가 타겟랩 지정 비율이 높다 → 우선순위 상위에 몰림
            target_lab = rng.random() < (0.45 if is_grad else 0.08)
            score = gpa + (1.5 if target_lab else 0.0) + (0.6 if is_grad else 0.0)
            tags = ["PRIMARY_JOB"]
            if target_lab:
                tags.append("TARGET_LAB")
            applicants.append(
                ApplicantIn(
                    applicant_id=str(serial),
                    name=rng.choice(_SURNAMES) + rng.choice(_GIVEN),
                    team=team,
                    degree=degree,
                    priority_score=round(score, 3),
                    target_lab=target_lab,
                    tags=tuple(tags),
                )
            )
    return applicants


def build_interviewers() -> list[InterviewerIn]:
    """팀당 4명(리더 1 + 실무 3).

    가능 시간은 덩어리(앞타임 · 뒤타임 · 모든타임)로만 준다. **못 나오는 날은
    두지 않는다** — 우리 모델에 담당자 가능 날이라는 것이 없어서다. 리더만
    뒤타임으로 두어 부하가 한쪽으로 몰리는지 볼 수 있게 하고, 실무진은
    모든타임이라 가용성 때문에 칸이 막히는 일이 없다.
    """
    interviewers: list[InterviewerIn] = []
    full = {day: list(HOURS) for day in DAYS}

    for t_idx, team in enumerate(TEAMS, start=1):
        # 리더: 뒤타임만 (부하 집중 방지) — 날은 가리지 않는다
        interviewers.append(
            InterviewerIn(
                interviewer_id=f"IV{t_idx}01",
                name=f"{team} 리더",
                team=team,
                title="수석",
                max_daily=4,
                priority=1,
                email=f"iv{t_idx}01@example.com",
                availability={day: list(BACK_HOURS) for day in DAYS},
            )
        )
        # 실무 3인: 모든타임
        for m_idx in range(3):
            interviewers.append(
                InterviewerIn(
                    interviewer_id=f"IV{t_idx}0{m_idx + 2}",
                    name=f"{team} 실무{m_idx + 1}",
                    team=team,
                    title=("책임", "선임", "선임")[m_idx],
                    max_daily=len(HOURS),
                    priority=2,
                    email=f"iv{t_idx}0{m_idx + 2}@example.com",
                    availability={day: list(hours) for day, hours in full.items()},
                )
            )
    return interviewers
