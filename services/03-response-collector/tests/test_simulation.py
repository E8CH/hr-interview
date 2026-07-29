"""명세 완료 판정: Before/After 시뮬레이션 — 회신 소요 30h → 12h 재현"""
import pytest

from app.services.simulation import (
    SimulationParams,
    build_population,
    format_report,
    run_simulation,
    simulate_after,
    simulate_before,
)


@pytest.fixture(scope="module")
def result():
    return run_simulation()


def test_before_is_about_30_hours(result):
    assert result["before"]["mean_hours"] == pytest.approx(30.0, abs=1.5)


def test_after_is_about_12_hours(result):
    assert result["after"]["mean_hours"] == pytest.approx(12.0, abs=1.5)


def test_reduction_meets_60pct_target(result):
    """비즈니스 가치: 회신 소요시간 60% 단축."""
    assert result["improvement"]["reduction_pct"] >= 55.0


def test_non_response_rate_improves(result):
    """비즈니스 가치: 미회신율 15% → 7%."""
    assert result["before"]["non_response_rate"] == pytest.approx(0.15, abs=0.05)
    assert result["after"]["non_response_rate"] == pytest.approx(0.07, abs=0.05)
    assert result["after"]["non_response_rate"] < result["before"]["non_response_rate"]


def test_tail_is_shortened(result):
    """자동 리마인더가 긴 꼬리를 잘라낸다."""
    assert result["after"]["p90_hours"] < result["before"]["p90_hours"] * 0.7


def test_deterministic_for_same_seed():
    assert run_simulation() == run_simulation()


def test_different_seed_changes_result():
    a = run_simulation(SimulationParams(seed=1))
    b = run_simulation(SimulationParams(seed=2))
    assert a["after"]["mean_hours"] != b["after"]["mean_hours"]


@pytest.mark.parametrize("seed", [1, 7, 99, 555, 12345])
def test_conclusion_holds_across_seeds(seed):
    """시드를 바꿔도 결론(대폭 단축)은 유지 — 우연한 시드 선택이 아님."""
    r = run_simulation(SimulationParams(seed=seed))
    assert 26.0 <= r["before"]["mean_hours"] <= 34.0
    assert r["after"]["mean_hours"] <= 14.0
    assert r["improvement"]["reduction_pct"] >= 50.0


def test_both_arms_share_same_population():
    """공정 비교 — 동일 모집단에 두 시나리오를 적용."""
    params = SimulationParams(n_invitees=50)
    population = build_population(params)
    assert simulate_before(params, population).total == 50
    assert simulate_after(params, population).total == 50


def test_reminders_are_the_lever():
    """리마인더 전환율을 0 으로 두면 개선폭이 크게 줄어든다."""
    params = SimulationParams()
    params.reminder_conversion = {1: 0.0, 2: 0.0, 3: 0.0}
    params.ignorer_conversion = {1: 0.0, 2: 0.0, 3: 0.0}
    no_reminder = run_simulation(params)

    assert no_reminder["after"]["non_response_rate"] > run_simulation()["after"]["non_response_rate"]


def test_report_is_renderable(result):
    report = format_report(result)
    assert "BEFORE" in report and "AFTER" in report
    assert "미회신율" in report


def test_empty_population_is_safe():
    result = run_simulation(SimulationParams(n_invitees=0))
    assert result["before"]["mean_hours"] == 0.0
    assert result["after"]["responded"] == 0
