from __future__ import annotations

from collections import OrderedDict
import os
import threading
import time
from typing import Any, Callable

from todoist_proxy.models import CacheEntry, CacheKey

DEFAULT_CACHE_TTL_SECONDS = 15.0
DEFAULT_CACHE_MAX_SIZE = 1024


class ResponseCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        max_size: int = DEFAULT_CACHE_MAX_SIZE,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._now_fn = now_fn
        self._entries: OrderedDict[CacheKey, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.ttl_seconds > 0 and self.max_size > 0

    def get(self, key: CacheKey) -> Any | None:
        if not self.enabled:
            return None

        with self._lock:
            now = self._now_fn()
            self._evict_expired_locked(now)
            entry = self._entries.get(key)
            if entry is None:
                return None

            if entry.expires_at <= now:
                self._entries.pop(key, None)
                return None

            self._entries.move_to_end(key)
            return entry.payload

    def set(self, key: CacheKey, payload: Any) -> None:
        if not self.enabled:
            return

        with self._lock:
            now = self._now_fn()
            self._evict_expired_locked(now)
            self._entries[key] = CacheEntry(payload=payload, expires_at=now + self.ttl_seconds)
            self._entries.move_to_end(key)

            while len(self._entries) > self.max_size:
                self._entries.popitem(last=False)

    def invalidate_token_scope(self, token_scope: str) -> None:
        if not self.enabled:
            return

        with self._lock:
            keys = [key for key in self._entries if key.token_scope == token_scope]
            for key in keys:
                self._entries.pop(key, None)

    def _evict_expired_locked(self, now: float) -> None:
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)


def build_response_cache() -> ResponseCache:
    ttl_seconds = _read_positive_float("TODOIST_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS)
    max_size = _read_positive_int("TODOIST_CACHE_MAX_SIZE", DEFAULT_CACHE_MAX_SIZE)
    return ResponseCache(ttl_seconds=ttl_seconds, max_size=max_size)


def _read_positive_float(env_name: str, default: float) -> float:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def _read_positive_int(env_name: str, default: int) -> int:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return value
