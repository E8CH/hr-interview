"""타 서비스 데이터 소스 어댑터

지원자 명단은 항상 Service 02(배포 확정 명단)에서 가져온다. 예전에는
USE_MOCK=true 이면 02를 호출하지 않고 목 데이터 88명을 지어냈는데, 그러면
시간표에 나오는 이름이 업로드한 취합파일 어디에도 없는 사람이 된다.
02 호출이 실패했을 때만(그리고 USE_MOCK=true 일 때만) 목 데이터로 폴백한다.

면접관 가용성도 같은 규칙으로 Service 03(회신 수집)에서 가져온다. 아직 아무도
회신하지 않아 가용 슬롯이 비어 있으면 시간표를 짤 수 없으므로, USE_MOCK=true
일 때만 목 면접관으로 폴백한다.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.domain.schemas import ApplicantIn, InterviewerIn
from app.infrastructure.contracts import HOURS, normalize_availability
from app.services import mock_data

logger = logging.getLogger(__name__)


class ApplicantSource:
    """Service 02 — 확정된 배포 계획의 지원자 명단"""

    MOCK_PLAN_IDS = {"", "mock-plan", "test-plan"}

    def fetch(self, round_id: str, plan_id: str) -> list[ApplicantIn]:
        if (plan_id or "") in self.MOCK_PLAN_IDS:
            # 배포안 없이 알고리즘만 돌려보는 경우 (단위 테스트 · 데모)
            return mock_data.build_applicants(round_id=round_id, plan_id=plan_id)
        try:
            return self._fetch_remote(plan_id)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            if not settings.use_mock:
                raise
            logger.warning(
                "02 확정 명단 조회 실패 — 목 데이터로 폴백한다 (plan_id=%s): %s",
                plan_id, exc,
            )
            return mock_data.build_applicants(round_id=round_id, plan_id=plan_id)

    def _fetch_remote(self, plan_id: str) -> list[ApplicantIn]:
        url = f"{settings.distributor_url}/api/v1/distribute/{plan_id}/applicants"
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        if not rows:
            raise ValueError(f"plan_id={plan_id} 확정 명단이 비어 있음")
        applicants = [
            ApplicantIn(
                applicant_id=str(r["applicant_id"]),
                name=r.get("name", ""),
                # team 은 02가 배정한 팀. team_1st(지망)와 다를 수 있으므로
                # 배정 결과인 team 을 우선한다.
                team=r.get("team") or r.get("team_1st", ""),
                degree=mock_data.normalize_degree(r.get("degree_type", "")),
                priority_score=float(r.get("gpa_final") or 0.0),
                target_lab=str(r.get("target_lab") or "").upper() == "Y",
                tags=tuple(r.get("reason_tags", [])),
            )
            for r in rows
        ]
        logger.info("확정 명단 %d명 로드 (출처=distributor, plan_id=%s)",
                    len(applicants), plan_id)
        return applicants


class AvailabilitySource:
    """Service 03 — 면접관 가용성 응답

    지원자 명단과 같은 규칙이다. 항상 03을 먼저 부르고, 회신이 아직 하나도
    없거나 03이 죽어 있을 때만(그리고 USE_MOCK=true 일 때만) 목 데이터를 쓴다.

    다만 폴백은 **다른 가용성이 아예 없을 때만** 쓸 수 있다. 면접관 마스터에
    적어 둔 가용성과 겹쳐 보는 자리에서 목 데이터를 받아 버리면, 지어낸 시간과
    실제 시간의 교집합만 남아 그분이 되는 칸이 통째로 사라진다. 그런 자리는
    allow_mock=False 로 부르고, 회신이 없으면 빈 명단을 받는다.
    """

    def fetch(self, round_id: str, allow_mock: bool = True) -> list[InterviewerIn]:
        try:
            return self._fetch_remote(round_id)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            if not allow_mock:
                logger.info(
                    "03 회신 없음 — 이 자리에서는 목으로 메우지 않는다 (round_id=%s): %s",
                    round_id, exc,
                )
                return []
            if not settings.use_mock:
                raise
            logger.warning(
                "03 가용성 조회 실패 — 목 데이터로 폴백한다 (round_id=%s): %s",
                round_id, exc,
            )
            return mock_data.build_interviewers()

    def _fetch_remote(self, round_id: str) -> list[InterviewerIn]:
        url = f"{settings.response_collector_url}/api/v1/rounds/{round_id}/availability"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        if not rows:
            raise ValueError(f"round_id={round_id} 회신된 가용성이 없음")
        interviewers = [
            InterviewerIn(
                interviewer_id=r["interviewer_id"],
                name=r.get("name", ""),
                team=r["team"],
                max_daily=int(r.get("max_daily") or len(HOURS)),
                priority=int(r.get("priority") or 2),
                email=r.get("email", ""),
                availability=normalize_availability(r.get("availability")),
            )
            for r in rows
            if r.get("availability")
        ]
        if not interviewers:
            raise ValueError(f"round_id={round_id} 가용 슬롯을 낸 면접관이 없음")
        logger.info("면접관 %d명 가용성 로드 (출처=response-collector, round_id=%s)",
                    len(interviewers), round_id)
        return interviewers


applicant_source = ApplicantSource()
availability_source = AvailabilitySource()
