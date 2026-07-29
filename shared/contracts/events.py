"""공통 이벤트 스키마 — 모든 서비스가 참조

00_SHARED_CONTRACT.md의 이벤트 카탈로그를 Pydantic으로 구현.
이 파일 수정은 모든 서비스에 영향 → 신중히 리뷰.
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from uuid import uuid4


class EventEnvelope(BaseModel):
    """모든 이벤트의 공통 봉투"""
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    round_id: str
    producer: str
    correlation_id: str
    payload: dict


# --- Service 01: Version Manager ---
class MasterRegisteredPayload(BaseModel):
    version_id: str
    fingerprint: str
    applicant_count: int
    actor: str


class DistributionRegisteredPayload(BaseModel):
    version_id: str
    team_name: str
    fingerprint: str
    applicant_count: int
    actor: str


class IntegrityViolatedPayload(BaseModel):
    status: str
    duplicate_count: int
    undistributed_count: int
    issues: list


# --- Service 02: Distributor ---
class DistributionPlanCreatedPayload(BaseModel):
    plan_id: str
    team_counts: dict
    total_applicants: int


class DistributionApprovedPayload(BaseModel):
    plan_id: str
    approver: str
    total_applicants: int


# --- Service 03: Response Collector ---
class RequestSentPayload(BaseModel):
    request_id: str
    invitee_count: int
    deadline: datetime


class ResponseReceivedPayload(BaseModel):
    response_id: str
    invitee_id: str
    submitted_at: datetime


class ReminderSentPayload(BaseModel):
    invitee_id: str
    level: int
    channel: str


# --- Service 04: Scheduler ---
class ScheduleGeneratedPayload(BaseModel):
    schedule_id: str
    total_assigned: int
    coverage_pct: float
    rule_compliance_overall: float


class ScheduleLockedPayload(BaseModel):
    schedule_id: str
    lock_level: str
    assignments_count: int


# --- Service 05: Repair Engine ---
class NoshowReportedPayload(BaseModel):
    noshow_applicant_ids: list
    reported_by: str


class RepairExecutedPayload(BaseModel):
    event_id: str
    plan_type: str
    rebooked: int
    deferred: int


# --- Service 06: Notification Hub ---
class NotificationSentPayload(BaseModel):
    notification_id: str
    recipient: str
    channel: str


# --- 이벤트 타입 상수 ---
class EventType:
    MASTER_REGISTERED = "MASTER_REGISTERED"
    DISTRIBUTION_REGISTERED = "DISTRIBUTION_REGISTERED"
    INTEGRITY_VIOLATED = "INTEGRITY_VIOLATED"
    DISTRIBUTION_PLAN_CREATED = "DISTRIBUTION_PLAN_CREATED"
    DISTRIBUTION_APPROVED = "DISTRIBUTION_APPROVED"
    DISTRIBUTION_ADJUSTED = "DISTRIBUTION_ADJUSTED"
    REQUEST_SENT = "REQUEST_SENT"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    REMINDER_SENT = "REMINDER_SENT"
    NON_RESPONDER_ESCALATED = "NON_RESPONDER_ESCALATED"
    SCHEDULE_GENERATED = "SCHEDULE_GENERATED"
    SCHEDULE_LOCKED = "SCHEDULE_LOCKED"
    RULE_VIOLATED = "RULE_VIOLATED"
    NOSHOW_REPORTED = "NOSHOW_REPORTED"
    REPAIR_EXECUTED = "REPAIR_EXECUTED"
    PARTICIPANT_DEFERRED = "PARTICIPANT_DEFERRED"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
