"""위험 신호 룰 엔진 4종

    담당자_변경 · 노쇼_예측 · 면접관_피로도 · 조직_회신_지연
"""
from __future__ import annotations

from app.events import EventType
from app.infrastructure.repository import EventRepository
from app.services import risk_detector as rd
from app.services.demo_data import DEMO_ROUND, SUBSTITUTE_RESPONDER
from app.services.event_collector import ingest_many
from tests.conftest import make_event

ROUND = "R2026-Q3-01"


def events_of(session, round_id: str = ROUND):
    return EventRepository(session).by_round(round_id)


# --- 룰 1: 담당자 변경 -------------------------------------------------------


def test_detects_responder_outside_invited_set(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제3기술원", "invitee_count": 2,
                         "invitee_ids": ["IV301", "IV302"]},
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제3기술원", "invitee_id": "IV-SUB-01"},
                minutes=10,
            ),
        ],
    )

    signals = rd.detect_responder_change(events_of(session))

    assert len(signals) == 1
    assert signals[0].type == "담당자_변경"
    assert signals[0].team == "제3기술원"
    assert signals[0].severity == "medium"


def test_no_signal_when_responder_was_invited(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제1기술원", "invitee_count": 2,
                         "invitee_ids": ["IV101", "IV102"]},
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제1기술원", "invitee_id": "IV101"},
                minutes=10,
            ),
        ],
    )
    assert rd.detect_responder_change(events_of(session)) == []


def test_explicit_previous_responder_flag_is_honoured(session):
    """초대 목록이 없어도 payload가 교체를 명시하면 감지한다"""
    ingest_many(
        session,
        [
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제2사업부", "invitee_id": "IV-NEW",
                         "previous_responder": "IV-OLD"},
            )
        ],
    )
    signals = rd.detect_responder_change(events_of(session))
    assert [s.team for s in signals] == ["제2사업부"]


# --- 룰 2: 노쇼 예측 ---------------------------------------------------------


def test_noshow_score_accumulates_across_signals(session):
    """노쇼 이력(0.5) + 연기(0.4) = 0.9 > 0.7 임계"""
    ingest_many(
        session,
        [
            make_event(
                EventType.NOSHOW_REPORTED,
                payload={"noshow_applicant_ids": ["A-1"]},
            ),
            make_event(
                EventType.PARTICIPANT_DEFERRED,
                payload={"participant_ids": ["A-1"]},
                minutes=5,
            ),
        ],
    )

    scores = rd.predict_noshow_scores(events_of(session))
    assert scores["A-1"] == 0.9

    signals = rd.detect_noshow_risk(events_of(session))
    assert len(signals) == 1
    assert signals[0].type == "노쇼_예측"
    assert signals[0].count == 1
    assert signals[0].severity == "low"


def test_noshow_below_threshold_is_not_reported(session):
    ingest_many(
        session,
        [make_event(EventType.NOSHOW_REPORTED, payload={"applicant_ids": ["A-9"]})],
    )
    assert rd.predict_noshow_scores(events_of(session))["A-9"] == 0.5
    assert rd.detect_noshow_risk(events_of(session)) == []


def test_explicit_noshow_score_in_payload_wins(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.REMINDER_SENT,
                payload={"invitee_id": "A-7", "applicant_id": "A-7", "noshow_score": 0.95},
            )
        ],
    )
    assert rd.predict_noshow_scores(events_of(session))["A-7"] == 0.95


def test_noshow_severity_scales_with_count(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.NOSHOW_REPORTED,
                payload={"noshow_applicant_ids": [f"A-{i}" for i in range(5)]},
            ),
            make_event(
                EventType.PARTICIPANT_DEFERRED,
                payload={"participant_ids": [f"A-{i}" for i in range(5)]},
                minutes=1,
            ),
        ],
    )
    signals = rd.detect_noshow_risk(events_of(session))
    assert signals[0].count == 5
    assert signals[0].severity == "medium"


# --- 룰 3: 면접관 피로도 -----------------------------------------------------


def test_three_consecutive_high_load_rounds_are_high_severity(session):
    for index, round_id in enumerate(["R2026-Q1-01", "R2026-Q2-01", "R2026-Q3-01"]):
        ingest_many(
            session,
            [
                make_event(
                    EventType.SCHEDULE_GENERATED,
                    round_id=round_id,
                    payload={"interviewer_loads": {"이OO": 26, "김OO": 10}},
                    minutes=index,
                )
            ],
        )

    signals = rd.detect_interviewer_fatigue(session, "R2026-Q3-01")

    assert [s.interviewer for s in signals] == ["이OO"]
    assert signals[0].severity == "high"
    assert signals[0].type == "면접관_피로도"


def test_two_consecutive_rounds_are_medium_severity(session):
    for index, round_id in enumerate(["R2026-Q1-01", "R2026-Q2-01", "R2026-Q3-01"]):
        loads = {"이OO": 10 if index == 0 else 30}
        ingest_many(
            session,
            [
                make_event(
                    EventType.SCHEDULE_GENERATED,
                    round_id=round_id,
                    payload={"interviewer_loads": loads},
                    minutes=index,
                )
            ],
        )

    signals = rd.detect_interviewer_fatigue(session, "R2026-Q3-01")
    assert signals[0].severity == "medium"


def test_fatigue_streak_resets_on_light_round(session):
    for index, round_id in enumerate(["R2026-Q1-01", "R2026-Q2-01", "R2026-Q3-01"]):
        loads = {"이OO": 5 if index == 2 else 30}
        ingest_many(
            session,
            [
                make_event(
                    EventType.SCHEDULE_GENERATED,
                    round_id=round_id,
                    payload={"interviewer_loads": loads},
                    minutes=index,
                )
            ],
        )
    assert rd.detect_interviewer_fatigue(session, "R2026-Q3-01") == []


def test_fatigue_accepts_list_payload_format(session):
    """`[{"interviewer": ..., "load": ...}]` 포맷도 읽는다"""
    for round_id in ["R2026-Q1-01", "R2026-Q2-01", "R2026-Q3-01"]:
        ingest_many(
            session,
            [
                make_event(
                    EventType.SCHEDULE_LOCKED,
                    round_id=round_id,
                    payload={"interviewers": [{"interviewer": "박OO", "load": 25}]},
                )
            ],
        )
    signals = rd.detect_interviewer_fatigue(session, "R2026-Q3-01")
    assert [s.interviewer for s in signals] == ["박OO"]


# --- 룰 4: 조직 회신 지연 ----------------------------------------------------


def test_org_delay_triggers_above_40h(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제3기술원", "invitee_count": 2,
                         "invitee_ids": ["a", "b"]},
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제3기술원", "invitee_id": "a", "response_hours": 52.0},
                minutes=1,
            ),
        ],
    )

    signals = rd.detect_org_delay(session, ROUND)

    assert len(signals) == 1
    assert signals[0].type == "조직_회신_지연"
    assert signals[0].team == "제3기술원"
    assert signals[0].severity == "medium"


def test_org_delay_is_high_above_72h(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제5본부", "invitee_count": 1, "invitee_ids": ["a"]},
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제5본부", "invitee_id": "a", "response_hours": 100.0},
                minutes=1,
            ),
        ],
    )
    assert rd.detect_org_delay(session, ROUND)[0].severity == "high"


def test_fast_org_produces_no_delay_signal(session):
    ingest_many(
        session,
        [
            make_event(
                EventType.REQUEST_SENT,
                payload={"org": "제1기술원", "invitee_count": 1, "invitee_ids": ["a"]},
            ),
            make_event(
                EventType.RESPONSE_RECEIVED,
                payload={"org": "제1기술원", "invitee_id": "a", "response_hours": 6.0},
                minutes=1,
            ),
        ],
    )
    assert rd.detect_org_delay(session, ROUND) == []


# --- 통합 -------------------------------------------------------------------


def test_all_four_rules_fire_on_demo_round(seeded):
    """위험 신호 4종 감지 로직 동작 (완료 판정 체크리스트 5)"""
    signals = rd.detect_risks(seeded, DEMO_ROUND)
    types = {s.type for s in signals}

    assert types == {"담당자_변경", "노쇼_예측", "면접관_피로도", "조직_회신_지연"}
    assert len(rd.RISK_RULES) == 4

    by_type = {s.type: s for s in signals}
    assert by_type["담당자_변경"].team == "제3기술원"
    assert by_type["조직_회신_지연"].team == "제3기술원"
    assert by_type["면접관_피로도"].interviewer == "이OO"
    assert by_type["노쇼_예측"].count == 3
    assert SUBSTITUTE_RESPONDER in (by_type["담당자_변경"].detail or "")


def test_overall_risk_level_takes_max_severity():
    from app.domain.kpi import RiskSignal

    def level(*severities):
        return rd.overall_risk_level(
            [RiskSignal(type="t", severity=s) for s in severities]
        )

    assert level() == "Low"
    assert level("low") == "Low"
    assert level("low", "medium") == "Medium"
    assert level("low", "medium", "high") == "High"


def test_detect_risks_on_empty_round_is_quiet(session):
    assert rd.detect_risks(session, "R-EMPTY") == []
    assert rd.overall_risk_level([]) == "Low"
