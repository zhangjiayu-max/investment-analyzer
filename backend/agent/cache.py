import hashlib
import time
import threading
from typing import Optional, Any


class ExpertCache:
    """Simple TTL-based cache for expert agent responses."""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 100):
        self._cache: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def _make_key(self, query: str, expert_name: str, user_id: str = "") -> str:
        raw = f"{query}|{expert_name}|{user_id}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, query: str, expert_name: str, user_id: str = "") -> Optional[Any]:
        key = self._make_key(query, expert_name, user_id)
        with self._lock:
            if key in self._cache:
                value, ts = self._cache[key]
                if time.time() - ts < self._ttl:
                    self._hits += 1
                    return value
                else:
                    del self._cache[key]
            self._misses += 1
            return None

    def put(self, query: str, expert_name: str, value: Any, user_id: str = ""):
        key = self._make_key(query, expert_name, user_id)
        with self._lock:
            if len(self._cache) >= self._max_size:
                oldest_key = min(self._cache, key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]
            self._cache[key] = (value, time.time())

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0,
            "size": len(self._cache)
        }


# Global singleton
expert_cache = ExpertCache()
