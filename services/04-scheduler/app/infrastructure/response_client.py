"""타 서비스 데이터 소스 어댑터

USE_MOCK=true (PoC 기본) → 로컬 목 데이터 생성기 사용.
USE_MOCK=false → Service 02(배포 확정 명단) / Service 03(면접관 응답) HTTP 호출.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.domain.schemas import ApplicantIn, InterviewerIn
from app.services import mock_data

logger = logging.getLogger(__name__)


class ApplicantSource:
    """Service 02 — 확정된 배포 계획의 지원자 명단"""

    def fetch(self, round_id: str, plan_id: str) -> list[ApplicantIn]:
        if settings.use_mock:
            return mock_data.build_applicants(round_id=round_id, plan_id=plan_id)
        url = f"{settings.distributor_url}/api/v1/plans/{plan_id}/applicants"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        return [
            ApplicantIn(
                applicant_id=str(r["applicant_id"]),
                name=r.get("name", ""),
                team=r.get("team_1st") or r.get("team", ""),
                degree=mock_data.normalize_degree(r.get("degree_type", "")),
                priority_score=float(r.get("gpa_final") or 0.0),
                target_lab=bool(r.get("target_lab")),
                tags=tuple(r.get("reason_tags", [])),
            )
            for r in rows
        ]


class AvailabilitySource:
    """Service 03 — 면접관 가용성 응답"""

    def fetch(self, round_id: str) -> list[InterviewerIn]:
        if settings.use_mock:
            return mock_data.build_interviewers()
        url = f"{settings.response_collector_url}/api/v1/rounds/{round_id}/availability"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        return [
            InterviewerIn(
                interviewer_id=r["interviewer_id"],
                name=r.get("name", ""),
                team=r["team"],
                max_daily=int(r.get("max_daily", 6)),
                priority=int(r.get("priority", 2)),
                email=r.get("email", ""),
                availability=r.get("availability", {}),
            )
            for r in rows
        ]


applicant_source = ApplicantSource()
availability_source = AvailabilitySource()
