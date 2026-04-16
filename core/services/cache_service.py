import json
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger


class CacheService:
    def __init__(self, cache_dir: Path = Path("data/cache"), ttl_minutes: int = 30):
        self.cache_dir = Path(cache_dir)
        self.ttl = timedelta(minutes=ttl_minutes)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(":", "_").replace("^", "")
        return self.cache_dir / f"{safe_key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            cached_at = datetime.fromisoformat(data["_cached_at"])
            if datetime.utcnow() - cached_at > self.ttl:
                logger.debug(f"Cache expirado: {key}")
                return None
            return data["payload"]
        except (json.JSONDecodeError, KeyError):
            return None

    def set(self, key: str, payload: dict) -> None:
        path = self._path(key)
        wrapper = {"_cached_at": datetime.utcnow().isoformat(), "payload": payload}
        path.write_text(json.dumps(wrapper, default=str, ensure_ascii=False))
        logger.debug(f"Cache guardado: {key}")

    def invalidate(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()
            logger.debug(f"Cache invalidado: {key}")

    def clear_all(self) -> None:
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
        logger.info("Cache limpiado completamente")
