"""명세 요구: test_validator — 정상/불량 응답 판정"""
import pytest

from app.services.validator import MAX_SLOTS, normalize_payload, validate_form_response


def test_valid_payload_passes(valid_payload):
    ok, reason = validate_form_response(valid_payload)
    assert ok is True
    assert reason == "OK"


def test_minimal_payload_passes():
    ok, reason = validate_form_response(
        {"job_role": "직무다", "available_slots": [{"day": "화", "hour": "10시"}]}
    )
    assert ok, reason


@pytest.mark.parametrize("missing", ["job_role", "available_slots"])
def test_missing_required_field(valid_payload, missing):
    payload = {k: v for k, v in valid_payload.items() if k != missing}
    ok, reason = validate_form_response(payload)
    assert ok is False
    assert reason == f"필수 누락: {missing}"


def test_empty_slots_rejected(valid_payload):
    valid_payload["available_slots"] = []
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert reason == "슬롯 없음"


def test_blank_job_role_rejected(valid_payload):
    valid_payload["job_role"] = "   "
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "job_role" in reason


@pytest.mark.parametrize(
    "bad_slot",
    [{"day": "화"}, {"hour": "10시"}, {}, "화 10시", 42],
)
def test_malformed_slot_rejected(valid_payload, bad_slot):
    valid_payload["available_slots"] = [bad_slot]
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "슬롯 형식 오류" in reason


def test_unknown_day_rejected(valid_payload):
    valid_payload["available_slots"] = [{"day": "토", "hour": "10시"}]
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "요일" in reason


def test_unknown_hour_rejected(valid_payload):
    valid_payload["available_slots"] = [{"day": "화", "hour": "13시"}]
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "시간대" in reason


def test_duplicate_slot_rejected(valid_payload):
    valid_payload["available_slots"] = [
        {"day": "화", "hour": "10시"},
        {"day": "화", "hour": "10시"},
    ]
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "중복 슬롯" in reason


def test_slot_count_cap_is_grid_size():
    """요일 5 × 시간대 6 = 30칸"""
    assert MAX_SLOTS == 30


@pytest.mark.parametrize("bad", [0, 7, "6", True])
def test_max_daily_invalid(valid_payload, bad):
    valid_payload["max_daily"] = bad
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "max_daily" in reason


def test_max_daily_optional(valid_payload):
    valid_payload.pop("max_daily")
    assert validate_form_response(valid_payload)[0] is True


def test_backup_contact_must_look_like_email(valid_payload):
    valid_payload["backup_contact"] = "그냥문자열"
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "backup_contact" in reason


def test_backup_contact_may_be_empty(valid_payload):
    valid_payload["backup_contact"] = None
    assert validate_form_response(valid_payload)[0] is True


def test_non_dict_payload_rejected():
    ok, reason = validate_form_response(["not", "a", "dict"])
    assert ok is False
    assert "payload 형식 오류" in reason


def test_slots_not_a_list(valid_payload):
    valid_payload["available_slots"] = {"day": "화", "hour": "10시"}
    ok, reason = validate_form_response(valid_payload)
    assert ok is False
    assert "배열" in reason


def test_normalize_fills_defaults():
    payload = {
        "job_role": "  배터리 소재 연구  ",
        "available_slots": [{"day": "화", "hour": "10시", "extra": "무시됨"}],
    }
    normalized = normalize_payload(payload)
    assert normalized["job_role"] == "배터리 소재 연구"
    assert normalized["available_slots"] == [{"day": "화", "hour": "10시"}]
    assert normalized["max_daily"] == 6
    assert normalized["backup_contact"] is None
    assert normalized["notes"] is None
