"""Cache with Redis when REDIS_URL is set, otherwise a small in-process TTL cache.

What is cached (and what is not):
  cached  : knowledge-base answers (keyed by normalised query + knowledge-base version),
            product lookups (public data)
  never   : orders, customer details, tickets, anything that identifies a person

Invalidation: knowledge answers embed a `kb_version` in their key. Uploading or deleting a
document bumps the version, so every stale answer becomes unreachable at once (and expires by
TTL). Product entries are deleted by key if a product changes, and expire by TTL otherwise.
"""
import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)


class Cache(ABC):
    backend: str

    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl: int) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def incr(self, key: str) -> int: ...


class InMemoryCache(Cache):
    backend = "memory"

    def __init__(self):
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.time():
                del self._data[key]
                return None
            return value

    def set(self, key, value, ttl):
        with self._lock:
            self._data[key] = (time.time() + ttl, value)

    def delete(self, key):
        with self._lock:
            self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()

    def incr(self, key):
        with self._lock:
            _, cur = self._data.get(key, (0, 0))
            self._data[key] = (float("inf"), int(cur) + 1)
            return int(cur) + 1


class RedisCache(Cache):
    backend = "redis"

    def __init__(self, url: str):
        import redis

        self._r = redis.Redis.from_url(url, socket_timeout=1.0, decode_responses=True)
        self._r.ping()

    def get(self, key):
        try:
            raw = self._r.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception as exc:  # cache must never break a request
            log.warning("redis get failed: %s", exc)
            return None

    def set(self, key, value, ttl):
        try:
            self._r.set(key, json.dumps(value), ex=ttl)
        except Exception as exc:
            log.warning("redis set failed: %s", exc)

    def delete(self, key):
        try:
            self._r.delete(key)
        except Exception as exc:
            log.warning("redis delete failed: %s", exc)

    def incr(self, key):
        try:
            return int(self._r.incr(key))
        except Exception as exc:
            log.warning("redis incr failed: %s", exc)
            return int(time.time())


def get_cache(settings) -> Cache:
    if settings.redis_url:
        try:
            return RedisCache(settings.redis_url)
        except Exception as exc:
            log.warning("Redis unavailable (%s); using in-memory cache", exc)
    return InMemoryCache()


KB_VERSION_KEY = "kb:version"


def kb_version(cache: Cache) -> int:
    v = cache.get(KB_VERSION_KEY)
    return int(v) if v is not None else 0


def bump_kb_version(cache: Cache) -> int:
    return cache.incr(KB_VERSION_KEY)
