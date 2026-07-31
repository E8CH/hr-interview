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
Day = Literal["월", "화", "수", "목", "금"]
Hour = Literal["09시", "10시", "11시", "14시", "15시", "16시"]
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
    max_daily: int = 6
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
