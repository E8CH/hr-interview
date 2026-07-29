"""Before/After 회신 소요시간 시뮬레이션

명세 완료 판정: **회신 소요 30h → 12h 재현**

모델
----
각 면접위원은 "내재 응답 성향"(base_hours, 로그정규분포)을 가진다.

일부 인원은 "방치 성향(ignorer)"을 가져 자발적으로는 회신하지 않는다.

BEFORE — 자유 텍스트 이메일 회신 · 리마인더 수동
  1. 방치 성향자는 끝내 회신하지 않는다.
  2. 나머지는 내재 성향대로 회신하되, 자유 텍스트라 형식이 어긋나 재문의가 발생할
     확률이 있고 그만큼 지연이 더해진다.
  3. 자동 리마인더가 없어 긴 꼬리가 그대로 남는다 (마감 시점에 절단).

AFTER — 구조화 웹폼 · 24h/48h/68h 자동 리마인더
  1. 폼 작성 부담이 낮아 내재 성향 자체가 단축된다 (form_speedup).
  2. 재문의가 사라진다 (스키마 검증이 제출 시점에 통과를 보장).
  3. 각 리마인더 시점에 미회신자 일부가 즉시 회신하며 긴 꼬리가 잘린다.
  4. 방치 성향자도 리마인더(특히 Level 3 상급자 CC)로 일부가 회신 전환된다.

난수는 시드 고정 → 결과 재현 가능.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import asdict, dataclass, field

from app.services.reminder_engine import REMINDER_RULES


@dataclass
class SimulationParams:
    n_invitees: int = 200
    seed: int = 20260729

    # 내재 미루기 성향 (로그정규) — BEFORE 평균 ≈ 30h 가 되도록 보정
    log_mu: float = 3.10
    log_sigma: float = 0.78

    # 회신 마감 (미회신 절단 시점)
    deadline_hours: float = 96.0

    # 자발적으로는 회신하지 않는 방치 성향 비율
    ignorer_prob: float = 0.11

    # BEFORE: 자유 텍스트 → 형식 오류로 HR 재문의 왕복
    rework_prob: float = 0.30
    rework_penalty_hours: float = 9.0

    # AFTER: 미루기 시간 배율.
    # 자유 텍스트는 "가능 시간대를 문장으로 정리"하는 부담 때문에 착수 자체가 미뤄지지만,
    # 폼은 30칸 클릭 1분이라 미루기 동기가 사라진다.
    form_speedup: float = 0.33
    # AFTER: 리마인더 수신 후 실제 회신까지 걸리는 시간
    reminder_response_lag_hours: float = 1.5
    # AFTER: 각 레벨 리마인더의 즉시 회신 전환율 (일반 미회신자)
    reminder_conversion: dict[int, float] = field(
        default_factory=lambda: {1: 0.72, 2: 0.80, 3: 0.88}
    )
    # AFTER: 방치 성향자의 레벨별 회신 전환율 (Level 3 은 상급자 CC 효과로 가장 높음)
    ignorer_conversion: dict[int, float] = field(
        default_factory=lambda: {1: 0.08, 2: 0.10, 3: 0.14}
    )


@dataclass
class ArmResult:
    """한쪽 시나리오(before/after)의 집계 결과."""

    label: str
    mean_hours: float
    median_hours: float
    p90_hours: float
    responded: int
    total: int

    @property
    def response_rate(self) -> float:
        return self.responded / self.total if self.total else 0.0

    @property
    def non_response_rate(self) -> float:
        return 1.0 - self.response_rate


def _summarize(label: str, hours: list[float], total: int) -> ArmResult:
    if not hours:
        return ArmResult(label, 0.0, 0.0, 0.0, 0, total)
    ordered = sorted(hours)
    p90_index = max(0, int(round(0.9 * len(ordered))) - 1)
    return ArmResult(
        label=label,
        mean_hours=round(statistics.fmean(ordered), 2),
        median_hours=round(statistics.median(ordered), 2),
        p90_hours=round(ordered[p90_index], 2),
        responded=len(ordered),
        total=total,
    )


@dataclass(frozen=True)
class Invitee:
    """시뮬레이션 대상 1명."""

    base_hours: float
    is_ignorer: bool


def build_population(params: SimulationParams) -> list[Invitee]:
    """두 시나리오가 **동일 모집단**을 공유하도록 한 번만 생성한다."""
    rng = random.Random(params.seed)
    return [
        Invitee(
            base_hours=rng.lognormvariate(params.log_mu, params.log_sigma),
            is_ignorer=rng.random() < params.ignorer_prob,
        )
        for _ in range(params.n_invitees)
    ]


def simulate_before(params: SimulationParams, population: list[Invitee]) -> ArmResult:
    """자유 텍스트 이메일 · 자동 리마인더 없음."""
    rng = random.Random(params.seed + 1)
    hours = []
    for person in population:
        if person.is_ignorer:
            continue  # 수동 리마인더에 기대 → 끝내 미회신
        actual = person.base_hours
        if rng.random() < params.rework_prob:
            actual += params.rework_penalty_hours  # 형식 오류 → 재문의 왕복
        if actual <= params.deadline_hours:
            hours.append(actual)
    return _summarize("before", hours, len(population))


def simulate_after(params: SimulationParams, population: list[Invitee]) -> ArmResult:
    """구조화 웹폼 · 24h/48h/68h 자동 리마인더."""
    rng = random.Random(params.seed + 2)
    reminder_times = [(r["level"], float(r["hours_after_send"])) for r in REMINDER_RULES]

    hours = []
    for person in population:
        # 방치 성향자는 자발적 회신 없음 → 리마인더에만 반응
        actual = math.inf if person.is_ignorer else person.base_hours * params.form_speedup
        conversion = params.ignorer_conversion if person.is_ignorer else params.reminder_conversion

        for level, due in reminder_times:
            if actual <= due:
                break  # 리마인더 도래 전에 이미 회신
            if rng.random() < conversion[level]:
                actual = due + params.reminder_response_lag_hours
                break
        if actual <= params.deadline_hours:
            hours.append(actual)
    return _summarize("after", hours, len(population))


def run_simulation(params: SimulationParams | None = None) -> dict:
    """Before/After 시뮬레이션 실행."""
    params = params or SimulationParams()
    population = build_population(params)

    before = simulate_before(params, population)
    after = simulate_after(params, population)
    reduction = (before.mean_hours - after.mean_hours) / before.mean_hours if before.mean_hours else 0.0

    return {
        "params": asdict(params),
        "before": asdict(before) | {"non_response_rate": round(before.non_response_rate, 4)},
        "after": asdict(after) | {"non_response_rate": round(after.non_response_rate, 4)},
        "improvement": {
            "mean_hours_saved": round(before.mean_hours - after.mean_hours, 2),
            "reduction_pct": round(reduction * 100, 1),
            "non_response_before_pct": round(before.non_response_rate * 100, 1),
            "non_response_after_pct": round(after.non_response_rate * 100, 1),
        },
    }


def format_report(result: dict) -> str:
    """콘솔 출력용 표."""
    b, a, imp = result["before"], result["after"], result["improvement"]
    return "\n".join(
        [
            "=" * 58,
            " Before/After 시뮬레이션 — 면접위원 회신 소요시간",
            "=" * 58,
            f" 표본: {b['total']}명 · 시드: {result['params']['seed']}",
            "-" * 58,
            f" {'지표':<22}{'BEFORE':>14}{'AFTER':>14}",
            "-" * 58,
            f" {'평균 회신 소요(h)':<20}{b['mean_hours']:>14.1f}{a['mean_hours']:>14.1f}",
            f" {'중앙값(h)':<22}{b['median_hours']:>14.1f}{a['median_hours']:>14.1f}",
            f" {'P90(h)':<24}{b['p90_hours']:>14.1f}{a['p90_hours']:>14.1f}",
            f" {'미회신율(%)':<22}{b['non_response_rate'] * 100:>14.1f}"
            f"{a['non_response_rate'] * 100:>14.1f}",
            "-" * 58,
            f" 단축: {imp['mean_hours_saved']}h ({imp['reduction_pct']}% 감소)",
            f" 미회신율: {imp['non_response_before_pct']}% → {imp['non_response_after_pct']}%",
            "=" * 58,
        ]
    )
