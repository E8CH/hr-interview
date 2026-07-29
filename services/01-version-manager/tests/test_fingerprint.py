"""지문 계산 테스트."""
from app.services.fingerprint import compute_fingerprint


def test_same_bytes_same_fingerprint():
    data = b"hello world"
    assert compute_fingerprint(data) == compute_fingerprint(data)


def test_fingerprint_is_16_chars():
    assert len(compute_fingerprint(b"anything")) == 16


def test_different_bytes_different_fingerprint():
    assert compute_fingerprint(b"a") != compute_fingerprint(b"b")


def test_known_value():
    # sha256("")[:16]
    assert compute_fingerprint(b"") == "e3b0c44298fc1c14"


def test_real_master_stable(master_bytes):
    assert compute_fingerprint(master_bytes) == compute_fingerprint(master_bytes)
    assert len(compute_fingerprint(master_bytes)) == 16
