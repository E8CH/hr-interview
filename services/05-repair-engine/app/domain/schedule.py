"""Service 04 에서 받아온 시간표 스냅샷 도메인 모델

재편성은 이 스냅샷 위에서만 수행된다. (다른 서비스 DB 직접 접근 금지)
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.repair_plan import SlotRef


class InterviewerInfo(BaseModel):
    interviewer_id: str
    name: str
    team: str
    max_daily: int = 6
    priority: int = 1


class ApplicantInfo(BaseModel):
    applicant_id: str
    name: str
    team_1st: str
    degree_type: str = "학사"          # "학사" | "대학원"


class ScheduleAssignment(BaseModel):
    assignment_id: str
    applicant_id: str
    interviewer_id: str
    day: str
    hour: str
    team: str
    lock_level: str = "DRAFT"
    reason_tags: list[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def slot(self) -> SlotRef:
        return SlotRef(day=self.day, hour=self.hour,
                       interviewer_id=self.interviewer_id, team=self.team)


class ScheduleSnapshot(BaseModel):
    schedule_id: str
    round_id: str
    lock_level: str = "CONFIRMED"
    assignments: list[ScheduleAssignment] = []
    reserved_slots: list[SlotRef] = []
    interviewers: list[InterviewerInfo] = []
    applicants: list[ApplicantInfo] = []

    def interviewer_map(self) -> dict[str, InterviewerInfo]:
        return {iv.interviewer_id: iv for iv in self.interviewers}

    def applicant_map(self) -> dict[str, ApplicantInfo]:
        return {ap.applicant_id: ap for ap in self.applicants}

    def assignment_of(self, applicant_id: str) -> ScheduleAssignment | None:
        for a in self.assignments:
            if a.applicant_id == applicant_id:
                return a
        return None
