"""Service 01 (version-manager) 클라이언트.

기본 경로: `master_version_id` 로 01에 원본 엑셀을 요청해 파싱한다.
회차에 실제로 업로드된 파일과 배포 명단이 반드시 일치해야 하므로 이게 정상 경로다.

01을 못 부르는 경우(서비스 미기동 등)에는 USE_MOCK=true 일 때만 폴백한다.
1. `MASTER_XLSX` 경로에 취합파일이 있으면 그것을 파싱 (기본 `./data/취합파일.xlsx`).
   백테스트가 이 경로를 탄다.
2. 그것도 없으면 시드 고정 합성 데이터.
   - 전체 467명, 그중 `결과P ∧ 구분R` 통과자 정확히 88명
   - 88 = 5개 팀 target_headcount 합(16+19+17+16+20)
   - 각 통과자는 최소 1개 팀에 배정 가능(조직 조건 충족)하도록 생성되어
     완전 매칭이 존재함 → 정원 오차 0 재현 가능

USE_MOCK=false 면 폴백 없이 01 실패를 그대로 올린다.
"""
from __future__ import annotations

import logging
import random
import zlib

import httpx
from contracts.types import Applicant

from app.config import settings
from app.domain.profile import TEAM_PROFILES
from app.infrastructure.master_excel import load_master_excel

logger = logging.getLogger(__name__)

TOTAL_MASTER = 467

JOBS = ["직무가", "직무나", "직무다", "직무라"]
ORGS = ["제1기술원", "제2사업부", "제3연구소"]
EXTRA_MAJORS = ["푸른화학과", "노을물리학과", "하늘기계공학과", "새벽통계학과"]
SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임"]
GIVEN = ["민준", "서연", "도윤", "하은", "지호", "예린", "시우", "수아", "건우", "다인"]

BACHELOR = "과정1"
GRAD_CODES = ["과정2", "과정3"]


class MasterNotFound(Exception):
    """Service 01에서 해당 version_id를 찾지 못함."""


def _all_majors() -> list[str]:
    majors: list[str] = []
    for row in TEAM_PROFILES:
        for major in row[3]:
            if major not in majors:
                majors.append(major)
    return majors + EXTRA_MAJORS


def _name(rng: random.Random) -> str:
    return rng.choice(SURNAMES) + rng.choice(GIVEN)


def _make_applicant(
    rng: random.Random,
    applicant_id: str,
    org: str,
    job: str,
    major: str,
    is_grad: bool,
    passes: bool,
) -> Applicant:
    return Applicant(
        applicant_id=applicant_id,
        name=_name(rng),
        team_1st=org,
        job_1st=job,
        rnd_type="구분R" if passes else rng.choice(["구분R", "구분N"]),
        degree_type=rng.choice(GRAD_CODES) if is_grad else BACHELOR,
        major_final=major,
        major_bachelor=rng.choice(_all_majors()) if rng.random() < 0.35 else major,
        gpa_final=round(rng.uniform(3.0, 4.5), 2),
        target_lab="Y" if rng.random() < 0.08 else "N",
        advisor=_name(rng) + "교수" if rng.random() < 0.12 else None,
        prev_applications=rng.choice([0, 0, 0, 1, 2]),
        doc_result="결과P" if passes else "결과F",
    )


def generate_mock_master(seed: int | None = None) -> list[Applicant]:
    """결정적 합성 마스터 467명 생성 (통과자 정확히 88명)."""
    rng = random.Random(seed if seed is not None else settings.mock_seed)
    majors_pool = _all_majors()
    applicants: list[Applicant] = []
    next_id = 3300000

    # --- 통과자 88명: 팀별 정원에 맞춰 설계 ---
    for name, primary, secondary, majors, orgs, ratio, headcount, _tags in TEAM_PROFILES:
        job_pool = primary or secondary or JOBS
        for idx in range(headcount):
            org = orgs[0] if (rng.random() < 0.6 or len(orgs) == 1) else rng.choice(orgs)
            job = rng.choice(job_pool) if rng.random() < 0.7 else rng.choice(JOBS)
            major = rng.choice(majors) if rng.random() < 0.7 else rng.choice(majors_pool)
            is_grad = idx < round(headcount * ratio)
            next_id += rng.randint(1, 9)
            applicants.append(
                _make_applicant(rng, str(next_id), org, job, major, is_grad, passes=True)
            )

    # --- 비대상 379명: 서류 탈락(결과F) 또는 비R&D(구분N) ---
    while len(applicants) < TOTAL_MASTER:
        next_id += rng.randint(1, 9)
        applicant = _make_applicant(
            rng,
            str(next_id),
            rng.choice(ORGS),
            rng.choice(JOBS),
            rng.choice(majors_pool),
            rng.random() < 0.3,
            passes=False,
        )
        if rng.random() < 0.5:
            # 서류는 통과했으나 비R&D → 배포 대상 아님
            applicant.doc_result = "결과P"
            applicant.rnd_type = "구분N"
        else:
            applicant.doc_result = "결과F"
        applicants.append(applicant)

    rng.shuffle(applicants)
    return applicants


class VersionClient:
    """Service 01 조회 어댑터."""

    def __init__(self, base_url: str | None = None, use_mock: bool | None = None):
        self._base_url = base_url or settings.version_manager_url
        self._use_mock = settings.use_mock if use_mock is None else use_mock

    def fetch_master(self, master_version_id: str) -> list[Applicant]:
        """master_version_id 로 등록된 "그 파일"을 파싱해서 돌려준다.

        01에서 원본을 받아오는 것이 정상 경로다. 예전에는 USE_MOCK=true 이면
        01을 아예 건너뛰고 로컬 ./data/취합파일.xlsx 를 읽었는데, 그 결과
        업로드한 파일과 배포 결과가 서로 다른 명단이 될 수 있었다.
        이제는 항상 01을 먼저 시도하고, 실패했을 때만(그리고 USE_MOCK=true
        일 때만) 로컬 파일 → 합성 데이터 순으로 폴백한다.
        """
        try:
            return self._fetch_remote(master_version_id)
        except (MasterNotFound, httpx.HTTPError) as exc:
            if not self._use_mock:
                raise
            logger.warning(
                "01 조회 실패 — 로컬 폴백으로 진행한다 (version_id=%s): %s",
                master_version_id, exc,
            )

        path = settings.master_xlsx
        if path is not None:
            return load_master_excel(path)
        seed = settings.mock_seed + zlib.crc32(master_version_id.encode("utf-8")) % 1000
        return generate_mock_master(seed)

    def _fetch_remote(self, master_version_id: str) -> list[Applicant]:
        """01이 보관 중인 원본 엑셀을 내려받아 파싱한다."""
        url = f"{self._base_url}/api/v1/versions/by-id/{master_version_id}/file"
        try:
            response = httpx.get(url, timeout=30.0)
        except httpx.HTTPError as exc:  # pragma: no cover - 네트워크 예외
            raise MasterNotFound(f"version-manager 호출 실패: {exc}") from exc
        if response.status_code == 404:
            raise MasterNotFound(f"master_version_id={master_version_id} 없음")
        response.raise_for_status()
        applicants = load_master_excel(response.content)
        logger.info(
            "마스터 %d명 로드 (출처=version-manager, version_id=%s)",
            len(applicants), master_version_id,
        )
        return applicants
