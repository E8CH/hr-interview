"""팀 프로필 도메인 모델 + 초기 시드 데이터 (명세 02 §Model)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class TeamProfile(BaseModel):
    """배포 스코어링의 기준이 되는 팀 프로필 (6개 축)."""

    team_name: str
    primary_job: list[str] = Field(default_factory=list)
    secondary_job: list[str] = Field(default_factory=list)
    preferred_majors: list[str] = Field(default_factory=list)
    org_allowed: list[str] = Field(default_factory=list)
    grad_ratio_target: float = 0.30
    target_headcount: int
    special_tags: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None

    @field_validator("grad_ratio_target")
    @classmethod
    def _ratio_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("grad_ratio_target must be between 0 and 1")
        return v

    @field_validator("target_headcount")
    @classmethod
    def _headcount_positive(cls, v: int) -> int:
        if v < 0:
            raise ValueError("target_headcount must be >= 0")
        return v


class TeamProfileUpdate(BaseModel):
    """PUT /api/v1/profiles/{team_name} 요청 바디 (team_name은 경로에서 받음)."""

    primary_job: list[str] = Field(default_factory=list)
    secondary_job: list[str] = Field(default_factory=list)
    preferred_majors: list[str] = Field(default_factory=list)
    org_allowed: list[str] = Field(default_factory=list)
    grad_ratio_target: float = Field(default=0.30, ge=0.0, le=1.0)
    target_headcount: int = Field(ge=0)
    special_tags: list[str] = Field(default_factory=list)


# (team_name, primary_job, secondary_job, preferred_majors,
#  org_allowed, grad_ratio_target, target_headcount, special_tags)
TEAM_PROFILES: list[tuple] = [
    ("AI솔루션팀", ["직무다"], ["직무나", "직무라"],
     ["벼리재료학과", "미르지능학과", "빛솔전산학부"],
     ["제1기술원"], 0.30, 16, []),
    ("로봇응용기술팀", ["직무나"], ["직무가"],
     ["빛솔전산학부", "자람정보학과", "가온제1공학과"],
     ["제1기술원", "제2사업부"], 0.20, 19, ["타겟랩", "지도교수"]),
    ("미래혁신팀", [], ["직무나", "직무라", "직무가"],
     ["미르지능학과", "여울생산학과", "가온제1공학과"],
     ["제1기술원", "제2사업부"], 0.25, 17, []),
    ("배터리기술팀", ["직무가", "직무라"], ["직무다"],
     ["윤슬고분자학과", "나래제1공학부", "여울생산학과"],
     ["제1기술원", "제2사업부"], 0.20, 16, []),
    ("전극기술팀", ["직무나"], ["직무가"],
     ["해오름설계학과", "온누리연산학부", "한별나노학과"],
     ["제1기술원", "제2사업부"], 0.35, 20, []),
]

TEAM_NAMES: list[str] = [row[0] for row in TEAM_PROFILES]


def seed_profiles() -> list[TeamProfile]:
    """초기 5개 팀 프로필을 도메인 모델로 반환."""
    return [
        TeamProfile(
            team_name=name,
            primary_job=list(primary),
            secondary_job=list(secondary),
            preferred_majors=list(majors),
            org_allowed=list(orgs),
            grad_ratio_target=ratio,
            target_headcount=headcount,
            special_tags=list(tags),
        )
        for (name, primary, secondary, majors, orgs, ratio, headcount, tags) in TEAM_PROFILES
    ]
