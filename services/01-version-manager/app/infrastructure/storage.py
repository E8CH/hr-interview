"""로컬 파일 저장 (MinIO 대체 PoC)."""
from pathlib import Path

from app.config import get_settings


def _sanitize(part: str) -> str:
    """경로 구분자/상위경로 제거."""
    return (part or "").replace("/", "_").replace("\\", "_").replace("..", "_").strip() or "_"


def save_file(round_id: str, kind: str, team_name: str | None, fingerprint: str,
              file_name: str, data: bytes) -> str:
    """파일을 storage/{round}/{kind}/{team}/ 아래에 저장하고 저장 경로를 반환."""
    settings = get_settings()
    parts = [settings.storage_path, _sanitize(round_id), _sanitize(kind)]
    if team_name:
        parts.append(_sanitize(team_name))
    target_dir = Path(*[str(p) for p in parts])
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{fingerprint}_{_sanitize(file_name)}"
    target.write_bytes(data)
    return str(target)


def read_file(path: str) -> bytes:
    return Path(path).read_bytes()
