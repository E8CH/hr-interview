"""공통 도메인 타입 — 모든 서비스가 참조"""
from typing import Literal
from datetime import datetime
from pydantic import BaseModel, Field

# 리터럴 타입
RoundId = str
ApplicantId = str
InterviewerId = str
TeamName = Literal["AI솔루션팀", "로봇응용기술팀", "미래혁신팀",
                   "배터리기술팀", "전극기술팀"]
#: 면접 기간의 며칟날인지 — 달력 날짜가 아니다. 달력의 언제인지는 우리가 따지는
#: 값이 아니라서, 날 이름도 차례로만 센다. constants.DAYS 와 같은 값.
Day = Literal["1일차", "2일차", "3일차", "4일차", "5일차"]
#: 하루 여덟 자리 — 이름은 시각이 아니라 그날의 몇째 칸인지를 가리킨다.
#: 실제 몇 시 몇 분인지는 면접 진행 조건이 정한다. constants.HOURS 와 같은 값.
Hour = Literal["1타임", "2타임", "3타임", "4타임",
               "5타임", "6타임", "7타임", "8타임"]
Degree = Literal["학사", "대학원"]
LockLevel = Literal["DRAFT", "CONFIRMED", "LOCKED"]


class Applicant(BaseModel):
    applicant_id: str
    name: str
    team_1st: str
    job_1st: str
    rnd_type: str
    degree_type: str
    major_final: str | None = None
    major_bachelor: str | None = None
    gpa_final: float | None = None
    target_lab: str | None = None
    advisor: str | None = None
    prev_applications: int = 0
    doc_result: str
    #: 1단계에서 이 사람을 적어 낸 팀들 (취합파일 '담당팀' 컬럼). 두 팀이 같이
    #: 적어 냈으면 둘 다 들어 있다. 비어 있으면 아무 팀도 지목하지 않은 사람이다.
    assigned_teams: list[str] = Field(default_factory=list)


class Interviewer(BaseModel):
    interviewer_id: str
    name: str
    team: str
    max_daily: int = 8
    priority: int = 1
    email: str
    backup_email: str | None = None


class Assignment(BaseModel):
    assignment_id: str
    applicant_id: str
    interviewer_id: str
    day: str
    hour: str
    lock_level: str = "DRAFT"
    reason_tags: list[str] = []
    created_at: datetime
