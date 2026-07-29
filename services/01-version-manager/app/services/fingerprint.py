"""SHA-256 지문 계산.

명세 도메인 규칙: hashlib.sha256(file_bytes).hexdigest()[:16]
"""
import hashlib


def compute_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
