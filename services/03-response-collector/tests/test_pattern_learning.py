"""명세 요구: test_pattern_learning — 히스토리 200건 → 조직별 평균 계산"""
import random
import statistics

import pytest

from app.domain.org_pattern import SLOW_THRESHOLD_HOURS, OrgPattern
from app.services import pattern_learner
from app.services.pattern_learner import PatternStats, compute_stats, merge_stats

ORGS = ["제1기술원", "제2사업부", "제3기술원", "제4연구소"]


def _history(seed: int = 42, n: int = 200):
    """조직별로 다른 응답 성향을 가진 히스토리 200건."""
    rng = random.Random(seed)
    profiles = {
        "제1기술원": (6.0, 2.0),
        "제2사업부": (18.0, 5.0),
        "제3기술원": (52.0, 8.0),
        "제4연구소": (41.0, 3.0),
    }
    records = []
    for i in range(n):
        org = ORGS[i % len(ORGS)]
        mean, spread = profiles[org]
        records.append((org, max(0.1, rng.gauss(mean, spread))))
    return records


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats == PatternStats(0.0, 0.0, 0)
    assert stats.predicted_slow is False


def test_compute_stats_matches_statistics_module():
    values = [3.0, 7.5, 12.25, 30.0, 41.0]
    stats = compute_stats(values)
    assert stats.mean_hours == pytest.approx(statistics.fmean(values))
    assert stats.std_hours == pytest.approx(statistics.pstdev(values))
    assert stats.sample_count == 5


def test_merge_stats_matches_batch_computation():
    """증분(Welford) 결과 == 전체 재계산 결과."""
    values = [4.0, 9.0, 15.0, 2.5, 33.0, 51.0, 7.25]
    incremental = PatternStats(0.0, 0.0, 0)
    for v in values:
        incremental = merge_stats(incremental, v)

    batch = compute_stats(values)
    assert incremental.mean_hours == pytest.approx(batch.mean_hours)
    assert incremental.std_hours == pytest.approx(batch.std_hours)
    assert incremental.sample_count == batch.sample_count


def test_learn_from_200_records(db):
    records = _history()
    assert len(records) == 200

    pattern_learner.learn_from_history(db, records)
    db.commit()

    expected: dict[str, list[float]] = {}
    for org, hours in records:
        expected.setdefault(org, []).append(hours)

    for org, values in expected.items():
        row = db.get(OrgPattern, org)
        assert row is not None, org
        assert row.sample_count == len(values) == 50
        assert row.mean_hours == pytest.approx(statistics.fmean(values), rel=1e-9)
        assert row.std_hours == pytest.approx(statistics.pstdev(values), rel=1e-6)


def test_predicted_slow_threshold(db):
    pattern_learner.learn_from_history(db, _history())
    db.commit()

    fast = db.get(OrgPattern, "제1기술원")
    slow = db.get(OrgPattern, "제3기술원")

    assert fast.mean_hours < SLOW_THRESHOLD_HOURS
    assert fast.predicted_slow is False
    assert slow.mean_hours > SLOW_THRESHOLD_HOURS
    assert slow.predicted_slow is True


def test_predicted_slow_is_strictly_above_40(db):
    pattern_learner.record_response(db, "경계조직", 40.0)
    db.commit()
    assert db.get(OrgPattern, "경계조직").predicted_slow is False

    # 평균이 40.0 을 넘어서는 순간 slow 로 전환 (경계는 배타적)
    pattern_learner.record_response(db, "경계조직", 40.1)
    db.commit()
    row = db.get(OrgPattern, "경계조직")
    assert row.sample_count == 2
    assert row.mean_hours == pytest.approx(40.05)
    assert row.predicted_slow is True

    pattern_learner.record_response(db, "느린조직", 41.0)
    db.commit()
    assert db.get(OrgPattern, "느린조직").predicted_slow is True


def test_list_patterns_sorted_slowest_first(db):
    pattern_learner.learn_from_history(db, _history())
    db.commit()

    rows = pattern_learner.list_patterns(db)
    means = [r.mean_hours for r in rows]
    assert means == sorted(means, reverse=True)
    assert rows[0].org == "제3기술원"


def test_none_org_bucketed_as_unknown(db):
    pattern_learner.record_response(db, None, 12.0)
    pattern_learner.record_response(db, "   ", 8.0)
    db.commit()

    row = db.get(OrgPattern, pattern_learner.UNKNOWN_ORG)
    assert row.sample_count == 2
    assert row.mean_hours == pytest.approx(10.0)


def test_predict_delay_hours(db):
    assert pattern_learner.predict_delay_hours(db, "없는조직") is None

    pattern_learner.record_response(db, "제1기술원", 6.0)
    pattern_learner.record_response(db, "제1기술원", 10.0)
    db.commit()

    assert pattern_learner.predict_delay_hours(db, "제1기술원") == pytest.approx(8.0)


def test_single_sample_has_zero_std(db):
    row = pattern_learner.record_response(db, "단일조직", 15.0)
    db.commit()
    assert row.sample_count == 1
    assert row.mean_hours == pytest.approx(15.0)
    assert row.std_hours == 0.0
